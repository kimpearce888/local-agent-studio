//! Local Agent Studio core engine (Rust).

pub mod agents;
pub mod communication;
pub mod context;
pub mod migration;
pub mod providers;
pub mod runtime;
pub mod scheduler;
pub mod storage;

pub use agents::{Agent, AgentStatus, ProfileSpec, RetentionPolicy};
pub use communication::{
    BusyMode, DelegationPayload, DeliveryState, MessageKind,
    TransferPayload,
};
pub use providers::{
    AdapterRegistry, ChatMessage, ChatResult, ModelInfo,
    OpenAiCompatAdapter, ProviderError,
};
pub use runtime::{production_responder, AgentRuntime, AsyncResponder};
pub use scheduler::{InferenceScheduler, ModelKey};
pub use storage::CoreStore;
