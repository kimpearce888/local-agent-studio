//! Shared inference scheduler (mandate 12.1).
//!
//! Several independent agents share one model server. Each
//! (provider, model) key has bounded concurrency; requests queue with
//! round-robin fairness *per agent* (a busy agent cannot monopolise
//! the provider), and one agent holds at most `per_agent_inflight`
//! slots. Cancellation is dropping the future: the queue slot is
//! skipped when its turn comes.
//!
//! Slot handoff is by ownership transfer: a releasing task passes its
//! semaphore permit through the waiter's oneshot channel, so the
//! accounting can never drift.

use std::collections::{HashMap, VecDeque};
use std::sync::{Arc, Mutex};
use tokio::sync::{oneshot, OwnedSemaphorePermit, Semaphore};

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct ModelKey {
    pub provider: String,
    pub model: String,
}

struct Waiting {
    agent: String,
    tx: oneshot::Sender<OwnedSemaphorePermit>,
}

struct KeyState {
    permits: Arc<Semaphore>,
    waiting: VecDeque<Waiting>,
    /// agent id -> slots currently held.
    inflight: HashMap<String, usize>,
}

/// An inference slot. Dropping it hands the slot to the next agent
/// (fair rotation) or back to the pool.
pub struct SchedulerPermit {
    scheduler: Arc<InferenceScheduler>,
    key: ModelKey,
    agent: String,
    permit: Option<OwnedSemaphorePermit>,
}

impl std::fmt::Debug for SchedulerPermit {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>)
           -> std::fmt::Result {
        write!(f, "SchedulerPermit({}/{})", self.key.provider,
               self.key.model)
    }
}

pub struct InferenceScheduler {
    max_per_key: usize,
    per_agent_inflight: usize,
    keys: Mutex<HashMap<ModelKey, Arc<Mutex<KeyState>>>>,
}

impl InferenceScheduler {
    pub fn new(max_per_key: usize, per_agent_inflight: usize) -> Self {
        Self {
            max_per_key: max_per_key.max(1),
            per_agent_inflight: per_agent_inflight.max(1),
            keys: Mutex::new(HashMap::new()),
        }
    }

    fn key_state(&self, key: &ModelKey) -> Arc<Mutex<KeyState>> {
        let mut keys = self.keys.lock().unwrap();
        keys.entry(key.clone()).or_insert_with(|| {
            Arc::new(Mutex::new(KeyState {
                permits: Arc::new(Semaphore::new(self.max_per_key)),
                waiting: VecDeque::new(),
                inflight: HashMap::new(),
            }))
        }).clone()
    }

    /// Acquire an inference slot for `agent` on `key`.
    ///
    /// Fast path: a free slot with no *distinct* agent queued. Slow
    /// path: queue and wait; the releaser hands its permit over when
    /// it is this agent's fair turn.
    pub async fn acquire(self: &Arc<Self>, key: ModelKey, agent: &str)
                         -> SchedulerPermit {
        let state = self.key_state(&key);
        loop {
            let rx = {
                let mut st = state.lock().unwrap();
                let mine = *st.inflight.get(agent).unwrap_or(&0);
                let distinct_waiting = st.waiting.iter().any(
                    |w| w.agent != agent);
                let already_queued = st.waiting.iter().any(
                    |w| w.agent == agent);
                if mine < self.per_agent_inflight && !distinct_waiting
                    && !already_queued {
                    if let Ok(permit) = st.permits.clone()
                        .try_acquire_owned() {
                        *st.inflight.entry(agent.to_string())
                            .or_insert(0) += 1;
                        return SchedulerPermit {
                            scheduler: self.clone(), key,
                            agent: agent.into(),
                            permit: Some(permit),
                        };
                    }
                }
                let (tx, rx) = oneshot::channel();
                st.waiting.push_back(Waiting {
                    agent: agent.into(), tx });
                rx
            };
            match rx.await {
                Ok(permit) => return SchedulerPermit {
                    scheduler: self.clone(), key, agent: agent.into(),
                    permit: Some(permit),
                },
                // Releaser vanished (cannot happen today) - requeue.
                Err(_) => continue,
            }
        }
    }

    /// Release bookkeeping + fair handoff. Called by Drop.
    fn release(&self, key: &ModelKey, agent: &str,
               mut permit: OwnedSemaphorePermit) {
        let state = self.key_state(key);
        let mut st = state.lock().unwrap();
        if let Some(n) = st.inflight.get_mut(agent) {
            *n = n.saturating_sub(1);
            if *n == 0 { st.inflight.remove(agent); }
        }
        // Fair rotation: the first queued waiter within its per-agent
        // cap gets the slot; cancelled waiters are skipped (the permit
        // comes back from the failed send).
        while let Some(index) = fair_pick(&st.waiting, agent) {
            let waiter = st.waiting.remove(index).expect("picked");
            let mine = *st.inflight.get(&waiter.agent).unwrap_or(&0);
            if mine < self.per_agent_inflight {
                *st.inflight.entry(waiter.agent.clone()).or_insert(0)
                    += 1;
                match waiter.tx.send(permit) {
                    Ok(()) => return,   // permit ownership transferred
                    Err(back) => {
                        // Waiter cancelled: undo its inflight, keep the
                        // permit for the next in line.
                        permit = back;
                        if let Some(n) = st.inflight
                            .get_mut(&waiter.agent) {
                            *n = n.saturating_sub(1);
                            if *n == 0 {
                                st.inflight.remove(&waiter.agent);
                            }
                        }
                    }
                }
            }
        }
        drop(permit);   // no waiter: the slot returns to the pool
    }

    /// Queue depth for a key (diagnostics, UI queue status).
    pub fn queue_depth(&self, key: &ModelKey) -> usize {
        self.key_state(key).lock().unwrap().waiting.len()
    }

    /// Distinct agents currently holding slots on a key.
    pub fn running_agents(&self, key: &ModelKey) -> usize {
        self.key_state(key).lock().unwrap().inflight.len()
    }
}

impl Drop for SchedulerPermit {
    fn drop(&mut self) {
        if let Some(permit) = self.permit.take() {
            self.scheduler.release(&self.key, &self.agent, permit);
        }
    }
}

/// Fair pick: the first waiter from a *different* agent than the one
/// releasing (round-robin across distinct agents, mandate 12.1);
/// fallback: the first waiter.
fn fair_pick(waiting: &VecDeque<Waiting>,
             releasing_agent: &str) -> Option<usize> {
    waiting.iter().position(|w| w.agent != releasing_agent)
        .or_else(|| {
            if waiting.is_empty() { None } else { Some(0) }
        })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn key() -> ModelKey {
        ModelKey { provider: "lmstudio".into(),
                   model: "qwen3-8b".into() }
    }

    #[tokio::test]
    async fn bounded_concurrency_and_handoff() {
        let sched = Arc::new(InferenceScheduler::new(2, 1));
        let p1 = sched.acquire(key(), "a").await;
        let p2 = sched.acquire(key(), "b").await;
        assert_eq!(sched.running_agents(&key()), 2);
        let s3 = sched.clone();
        let k3 = key();
        let c = tokio::spawn(async move {
            s3.acquire(k3, "c").await
        });
        tokio::time::sleep(std::time::Duration::from_millis(20)).await;
        assert_eq!(sched.queue_depth(&key()), 1, "c waits");
        drop(p1);
        let p3 = tokio::time::timeout(
            std::time::Duration::from_millis(200), c).await
            .expect("c takes the freed slot").unwrap();
        assert_eq!(sched.running_agents(&key()), 2);
        drop(p2); drop(p3);
    }

    #[tokio::test]
    async fn one_agent_cannot_monopolise() {
        // Mandate 12.1 fairness: a spamming agent shares the provider.
        let sched = Arc::new(InferenceScheduler::new(1, 1));
        let order = Arc::new(Mutex::new(Vec::<String>::new()));
        let mut tasks = Vec::new();
        // "greedy" issues 4 sequential requests, "polite" 1; both start
        // at the same moment.
        for (agent, n) in [("greedy", 4usize), ("polite", 1usize)] {
            for _ in 0..n {
                let s = sched.clone();
                let log = order.clone();
                let a = agent.to_string();
                tasks.push(tokio::spawn(async move {
                    for _ in 0..1 {
                        let _p = s.acquire(key(), &a).await;
                        log.lock().unwrap().push(format!("{a}-start"));
                        tokio::time::sleep(
                            std::time::Duration::from_millis(15)).await;
                        log.lock().unwrap().push(format!("{a}-end"));
                    }
                }));
            }
        }
        for t in tasks { t.await.unwrap(); }
        let log = order.lock().unwrap().clone();
        let polite = log.iter().position(|e| e == "polite-start")
            .expect("polite ran");
        let greedy_starts: Vec<usize> = log.iter().enumerate()
            .filter(|(_, e)| *e == "greedy-start")
            .map(|(i, _)| i).collect();
        assert_eq!(greedy_starts.len(), 4);
        assert!(polite < greedy_starts[3],
                "polite must not be starved behind all greedy starts:\
                 {log:?}");
    }

    #[tokio::test]
    async fn per_agent_inflight_cap() {
        let sched = Arc::new(InferenceScheduler::new(4, 2));
        let _p1 = sched.acquire(key(), "a").await;
        let _p2 = sched.acquire(key(), "a").await;
        let s3 = sched.clone();
        let task = tokio::spawn(async move {
            s3.acquire(key(), "a").await
        });
        tokio::time::sleep(std::time::Duration::from_millis(30)).await;
        assert!(sched.queue_depth(&key()) >= 1,
                "third same-agent request waits behind its own two");
        drop(_p1);
        let _p3 = tokio::time::timeout(
            std::time::Duration::from_millis(200), task).await
            .expect("queued request proceeds after a slot frees")
            .unwrap();
    }

    #[tokio::test]
    async fn cancelled_waiter_is_skipped() {
        let sched = Arc::new(InferenceScheduler::new(1, 1));
        let holder = sched.acquire(key(), "a").await;
        let s2 = sched.clone();
        let cancelled = tokio::spawn(async move {
            s2.acquire(key(), "b").await
        });
        tokio::time::sleep(std::time::Duration::from_millis(20)).await;
        cancelled.abort();
        let s3 = sched.clone();
        let next = tokio::spawn(async move {
            s3.acquire(key(), "c").await
        });
        drop(holder);
        let p = tokio::time::timeout(
            std::time::Duration::from_millis(300), next).await
            .expect("c proceeds despite b's cancellation").unwrap();
        drop(p);
    }

    #[test]
    fn constructor_caps_are_sane() {
        let s = InferenceScheduler::new(2, 1);
        assert!(s.max_per_key >= 1 && s.per_agent_inflight >= 1);
    }
}
