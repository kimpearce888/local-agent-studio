//! OS-level sandboxing for local command execution (mandate 13.4).
//!
//! `run_command` executes ONLY inside a Landlock sandbox: the
//! child's filesystem access is restricted, before exec, to the
//! agent's workspace jail (read/write) plus system directories
//! (read/execute).  Commands are explicit ARGUMENT ARRAYS - never a
//! model-generated shell string.  Limits: wall-clock timeout with
//! process-tree cancellation (SIGKILL to the child's process
//! group), CPU/address-space/file-size rlimits, and capped output.
//!
//! **Fail closed** (mandate 13.4): if the kernel cannot provide
//! Landlock (`landlock_create_ruleset` unavailable), the command
//! REFUSES to run unsandboxed - the error says exactly that.  This
//! build sandbox's kernel is 5.10 (Landlock needs 5.13+), so the
//! fail-closed path is what runs here; CI's ubuntu runners run 6.x
//! kernels and exercise the real confinement paths (the tests
//! branch on the runtime probe and cover both honestly).
//!
//! The syscalls are issued through `libc` directly (no Landlock
//! crate): three calls, `landlock_create_ruleset` (444),
//! `landlock_add_rule` (445) with PATH_BENEATH rules, and
//! `landlock_restrict_self` (446) after PR_SET_NO_NEW_PRIVS.  The
//! syscall numbers are shared by x86_64 and aarch64 (asm-generic
//! table).  Everything a pre_exec callback touches is prepared
//! BEFORE the fork: no allocation and only raw syscalls inside.

use std::ffi::CString;
use std::os::unix::process::CommandExt;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

/// Maximum wall-clock time for one command.
pub const MAX_TIMEOUT_SECS: u64 = 60;
/// Default wall-clock time when the caller does not set one.
pub const DEFAULT_TIMEOUT_SECS: u64 = 20;
/// Combined stdout+stderr kept after a run (the rest is dropped,
/// and the truncation is reported).
pub const OUTPUT_CAP_BYTES: usize = 64 * 1024;
/// Address-space cap for the child (RLIMIT_AS).
const RLIMIT_AS_BYTES: u64 = 512 * 1024 * 1024;
/// File-size cap for the child (RLIMIT_FSIZE) - above the tool
/// engine's 5 MB write cap so legitimate writes fit and abuse does
/// not.
const RLIMIT_FSIZE_BYTES: u64 = 8 * 1024 * 1024;

// -- Landlock ABI ------------------------------------------------------

const SYS_LANDLOCK_CREATE_RULESET: libc::c_long = 444;
const SYS_LANDLOCK_ADD_RULE: libc::c_long = 445;
const SYS_LANDLOCK_RESTRICT_SELF: libc::c_long = 446;
const LANDLOCK_CREATE_RULESET_VERSION: libc::c_ulong = 1;
const LANDLOCK_RULE_PATH_BENEATH: libc::c_int = 1;

const ACCESS_EXECUTE: u64 = 1 << 0;
const ACCESS_WRITE_FILE: u64 = 1 << 1;
const ACCESS_READ_FILE: u64 = 1 << 2;
const ACCESS_READ_DIR: u64 = 1 << 3;
const ACCESS_REMOVE_DIR: u64 = 1 << 4;
const ACCESS_REMOVE_FILE: u64 = 1 << 5;
const ACCESS_MAKE_DIR: u64 = 1 << 7;
const ACCESS_MAKE_REG: u64 = 1 << 8;
const ACCESS_MAKE_SOCK: u64 = 1 << 9;
const ACCESS_MAKE_FIFO: u64 = 1 << 10;
const ACCESS_MAKE_SYM: u64 = 1 << 12;
const ACCESS_REFER: u64 = 1 << 13;      // ABI 2
const ACCESS_TRUNCATE: u64 = 1 << 14;   // ABI 3

#[repr(C)]
struct RulesetAttr {
    handled_access_fs: u64,
}

#[repr(C)]
struct PathBeneathAttr {
    allowed_access: u64,
    parent_fd: libc::c_int,
    _pad: u32,
}

/// The kernel's Landlock ABI version, or None when Landlock is
/// unavailable (pre-5.13, compiled out, or blocked by a container
/// seccomp policy).  Pure probe: flags=VERSION never installs a
/// ruleset.
pub fn landlock_abi() -> Option<u32> {
    let r = unsafe {
        libc::syscall(SYS_LANDLOCK_CREATE_RULESET,
                      std::ptr::null::<RulesetAttr>(), 0usize,
                      LANDLOCK_CREATE_RULESET_VERSION)
    };
    if r >= 0 { Some(r as u32) } else { None }
}

/// One pre-exec rule: `allowed_access` under `path`.
struct Rule {
    allowed_access: u64,
    path: CString,
}

fn system_roots() -> Vec<PathBuf> {
    // Where binaries and runtime data live on a normal Linux
    // (duplicates and missing dirs are harmless).
    ["/usr", "/lib", "/lib64", "/etc", "/bin", "/sbin"]
        .iter().map(|p| p.into()).collect()
}

/// Build the Landlock rules for one confined command.  The jail is
/// read-write (minus device nodes); system roots are read+execute.
/// Returns None-side rules cannot even be listed (e.g. no /usr).
fn landlock_rules(jail: &Path, abi: u32) -> Vec<Rule> {
    let mut handled = ACCESS_EXECUTE | ACCESS_WRITE_FILE
        | ACCESS_READ_FILE | ACCESS_READ_DIR | ACCESS_REMOVE_DIR
        | ACCESS_REMOVE_FILE | ACCESS_MAKE_DIR | ACCESS_MAKE_REG
        | ACCESS_MAKE_SOCK | ACCESS_MAKE_FIFO | ACCESS_MAKE_SYM;
    if abi >= 2 { handled |= ACCESS_REFER; }
    if abi >= 3 { handled |= ACCESS_TRUNCATE; }
    let _ = handled;   // handled_access_fs is computed in apply()

    let mut jail_access = ACCESS_READ_FILE | ACCESS_READ_DIR
        | ACCESS_WRITE_FILE | ACCESS_REMOVE_DIR | ACCESS_REMOVE_FILE
        | ACCESS_MAKE_DIR | ACCESS_MAKE_REG | ACCESS_MAKE_SOCK
        | ACCESS_MAKE_FIFO | ACCESS_MAKE_SYM;
    if abi >= 2 { jail_access |= ACCESS_REFER; }
    if abi >= 3 { jail_access |= ACCESS_TRUNCATE; }
    let ro_access = ACCESS_READ_FILE | ACCESS_READ_DIR
        | ACCESS_EXECUTE;

    let mut rules = Vec::new();
    if let Some(c) = path_to_cstring(jail) {
        rules.push(Rule { allowed_access: jail_access, path: c });
    }
    for root in system_roots() {
        if let Some(c) = path_to_cstring(&root) {
            rules.push(Rule { allowed_access: ro_access, path: c });
        }
    }
    rules
}

fn path_to_cstring(p: &Path) -> Option<CString> {
    CString::new(p.as_os_str().to_string_lossy().as_bytes()).ok()
}

/// Apply the Landlock ruleset in a pre_exec callback.  ONLY raw
/// syscalls happen here (async-signal-safe); the rules are prepared
/// before the fork.  The fd is closed with O_CLOEXEC-safe handling
/// on every path.
fn apply_landlock(rules: &[Rule], abi: u32) -> std::io::Result<()> {
    let mut handled = ACCESS_EXECUTE | ACCESS_WRITE_FILE
        | ACCESS_READ_FILE | ACCESS_READ_DIR | ACCESS_REMOVE_DIR
        | ACCESS_REMOVE_FILE | ACCESS_MAKE_DIR | ACCESS_MAKE_REG
        | ACCESS_MAKE_SOCK | ACCESS_MAKE_FIFO | ACCESS_MAKE_SYM;
    if abi >= 2 { handled |= ACCESS_REFER; }
    if abi >= 3 { handled |= ACCESS_TRUNCATE; }
    let attr = RulesetAttr { handled_access_fs: handled };
    let fd = unsafe {
        libc::syscall(SYS_LANDLOCK_CREATE_RULESET,
                      &attr as *const RulesetAttr,
                      std::mem::size_of::<RulesetAttr>(), 0u64)
    };
    if fd < 0 {
        return Err(std::io::Error::last_os_error());
    }
    let fd = fd as libc::c_int;
    for rule in rules {
        let parent_fd = unsafe {
            libc::open(rule.path.as_ptr(),
                       libc::O_PATH | libc::O_CLOEXEC)
        };
        if parent_fd < 0 {
            unsafe { libc::close(fd); }
            return Err(std::io::Error::last_os_error());
        }
        let mut attr = PathBeneathAttr {
            allowed_access: rule.allowed_access,
            parent_fd, _pad: 0,
        };
        // Rules referencing bits the kernel does not know are
        // refused with EINVAL: mask down to what this ABI handles.
        attr.allowed_access &= handled;
        let r = unsafe {
            libc::syscall(SYS_LANDLOCK_ADD_RULE, fd,
                          LANDLOCK_RULE_PATH_BENEATH,
                          &attr as *const PathBeneathAttr, 0u64)
        };
        unsafe { libc::close(parent_fd); }
        if r < 0 {
            unsafe { libc::close(fd); }
            return Err(std::io::Error::last_os_error());
        }
    }
    // no_new_privs is required before restricting.
    let r = unsafe {
        libc::prctl(libc::PR_SET_NO_NEW_PRIVS, 1u64, 0, 0, 0)
    };
    if r < 0 {
        unsafe { libc::close(fd); }
        return Err(std::io::Error::last_os_error());
    }
    let r = unsafe {
        libc::syscall(SYS_LANDLOCK_RESTRICT_SELF, fd, 0u64)
    };
    unsafe { libc::close(fd); }
    if r < 0 {
        return Err(std::io::Error::last_os_error());
    }
    Ok(())
}

/// Resolve a command name against PATH in the PARENT (the child
/// never searches anything we did not decide).  Absolute paths pass
/// through when they exist.
pub fn resolve_command(argv0: &str) -> Option<PathBuf> {
    let p = Path::new(argv0);
    if p.is_absolute() {
        return p.is_file().then(|| p.to_path_buf());
    }
    if argv0.contains('/') {
        return None;   // relative paths are not commands
    }
    let path = std::env::var_os("PATH")?;
    for dir in std::env::split_paths(&path) {
        let cand = dir.join(argv0);
        if cand.is_file() {
            return Some(cand);
        }
    }
    None
}

/// The outcome of one confined run.
#[derive(Debug, Clone, PartialEq)]
pub struct ConfinedRun {
    pub ok: bool,
    pub exit_code: Option<i32>,
    pub output: String,
    pub timed_out: bool,
    pub truncated: bool,
}

/// Run one command array inside the sandbox.
///
/// Fails closed: no Landlock -> Err, and the message says the
/// kernel cannot sandbox the command.  On success the child ran
/// with the jail as its only writable root, its own process group
/// (killed on timeout), rlimits, and capped output.
pub fn run_confined(
    jail: &Path,
    argv: &[String],
    timeout_secs: u64,
) -> Result<ConfinedRun, String> {
    if argv.is_empty() {
        return Err("run_command: argv must not be empty".into());
    }
    let Some(abi) = landlock_abi() else {
        return Err(
            "run_command refuses to run unsandboxed: this kernel \
             provides no Landlock (needs 5.13+); failing closed"
                .into());
    };
    let Some(exe) = resolve_command(&argv[0]) else {
        return Err(format!(
            "run_command: {:?} is not an executable on PATH",
            argv[0]));
    };
    let timeout = timeout_secs.clamp(1, MAX_TIMEOUT_SECS);
    let rules = landlock_rules(jail, abi);

    let mut cmd = Command::new(&exe);
    cmd.args(&argv[1..])
        .current_dir(jail)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    unsafe {
        // Everything below runs in the forked child, before exec.
        cmd.pre_exec(move || {
            // Own process group: timeout kills the whole tree.
            libc::setsid();
            let as_limit = libc::rlimit {
                rlim_cur: RLIMIT_AS_BYTES,
                rlim_max: RLIMIT_AS_BYTES };
            libc::setrlimit(libc::RLIMIT_AS, &as_limit);
            let fsize = libc::rlimit {
                rlim_cur: RLIMIT_FSIZE_BYTES,
                rlim_max: RLIMIT_FSIZE_BYTES };
            libc::setrlimit(libc::RLIMIT_FSIZE, &fsize);
            let cpu = libc::rlimit {
                rlim_cur: timeout as libc::rlim_t + 2,
                rlim_max: timeout as libc::rlim_t + 2 };
            libc::setrlimit(libc::RLIMIT_CPU, &cpu);
            let nofile = libc::rlimit {
                rlim_cur: 256, rlim_max: 256 };
            libc::setrlimit(libc::RLIMIT_NOFILE, &nofile);
            apply_landlock(&rules, abi)
        });
    }
    let mut child = cmd.spawn()
        .map_err(|e| format!("run_command spawn: {e}"))?;
    // The child never reads stdin: drop our end for a clean EOF.
    drop(child.stdin.take());

    // Reader threads end at pipe EOF, including after SIGKILL.
    let stdout_pipe = child.stdout.take()
        .ok_or("no stdout")?;
    let stderr_pipe = child.stderr.take()
        .ok_or("no stderr")?;
    let out_t = std::thread::spawn(move || {
        let mut buf = Vec::new();
        let _ = stdout_pipe.take((OUTPUT_CAP_BYTES + 1) as u64)
            .read_to_end(&mut buf);
        buf
    });
    let err_t = std::thread::spawn(move || {
        let mut buf = Vec::new();
        let _ = stderr_pipe.take((OUTPUT_CAP_BYTES + 1) as u64)
            .read_to_end(&mut buf);
        buf
    });

    let deadline = Instant::now() + Duration::from_secs(timeout);
    loop {
        match child.try_wait() {
            Ok(Some(status)) => {
                let mut out = out_t.join().unwrap_or_default();
                let err = err_t.join().unwrap_or_default();
                out.extend_from_slice(&err);
                let truncated = out.len() > OUTPUT_CAP_BYTES;
                out.truncate(OUTPUT_CAP_BYTES);
                let code = status.code();
                return Ok(ConfinedRun {
                    ok: status.success(),
                    exit_code: code,
                    output: String::from_utf8_lossy(&out)
                        .into_owned(),
                    timed_out: false,
                    truncated,
                });
            }
            Ok(None) => {
                if Instant::now() >= deadline {
                    // Process-tree cancellation: the child is its
                    // own group leader (setsid above).
                    unsafe {
                        libc::kill(-(child.id() as libc::pid_t),
                                   libc::SIGKILL);
                    }
                    let _ = child.wait();
                    let mut out = out_t.join().unwrap_or_default();
                    let err = err_t.join().unwrap_or_default();
                    out.extend_from_slice(&err);
                    out.truncate(OUTPUT_CAP_BYTES);
                    return Ok(ConfinedRun {
                        ok: false,
                        exit_code: None,
                        output: format!(
                            "run_command timed out after {timeout}s \
                             (process tree killed)\n{}",
                            String::from_utf8_lossy(&out)),
                        timed_out: true,
                        truncated: false,
                    });
                }
                std::thread::sleep(Duration::from_millis(20));
            }
            Err(e) => {
                return Err(format!("run_command wait: {e}"));
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn jail(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "las-sandbox-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn probe_reports_the_kernel_honestly() {
        match landlock_abi() {
            Some(abi) => eprintln!(
                "Landlock ABI {abi}: confinement tests will run"),
            None => eprintln!(
                "no Landlock on this kernel: fail-closed paths"),
        }
    }

    #[test]
    fn empty_argv_is_refused() {
        let err = run_confined(&jail("empty"), &[], 5)
            .expect_err("empty argv");
        assert!(err.contains("argv"), "{err}");
    }

    #[test]
    fn unknown_command_is_refused() {
        let err = run_confined(&jail("unk"),
                               &["definitely-not-a-command-xyz"
                                 .to_string()], 5)
            .expect_err("unknown command");
        // Which refusal comes first depends on what the kernel
        // offers: fail-closed dominates on Landlock-less kernels.
        if landlock_abi().is_none() {
            assert!(err.contains("Landlock"), "{err}");
        } else {
            assert!(err.contains("not an executable"), "{err}");
        }
    }

    #[test]
    fn fail_closed_or_confined_by_kernel_support() {
        // The single honest test: on kernels WITH Landlock the
        // command runs and stays inside the jail; on kernels
        // without it the command refuses to run at all.  Both are
        // correct mandate-13.4 behavior.
        let dir = jail("branch");
        match landlock_abi() {
            None => {
                let err = run_confined(&dir,
                    &["touch".into(), "ok.txt".into()], 10)
                    .expect_err("must fail closed");
                assert!(err.contains("Landlock"), "{err}");
                assert!(err.contains("failing closed"), "{err}");
            }
            Some(_) => {
                // Allowed: write inside the jail.
                let r = run_confined(&dir,
                    &["touch".into(), "ok.txt".into()], 10)
                    .unwrap();
                assert!(r.ok, "in-jail write allowed: {:?}", r);
                assert!(dir.join("ok.txt").exists());
                // Confined: writing outside the jail is refused.
                let escape = std::env::temp_dir().join(format!(
                    "las-escape-{}", std::process::id()));
                let r = run_confined(&dir,
                    &["touch".into(),
                      escape.to_string_lossy().into_owned()], 10)
                    .unwrap();
                assert!(!r.ok,
                        "escape write must be refused: {:?}", r);
                assert!(!escape.exists());
            }
        }
    }

    #[test]
    fn timeout_kills_the_process_tree() {
        let dir = jail("timeout");
        match landlock_abi() {
            None => {
                let err = run_confined(&dir,
                    &["sleep".into(), "30".into()], 1)
                    .expect_err("must fail closed");
                assert!(err.contains("Landlock"), "{err}");
            }
            Some(_) => {
                let start = Instant::now();
                let r = run_confined(&dir,
                    &["sleep".into(), "30".into()], 1).unwrap();
                assert!(r.timed_out, "{:?}", r);
                assert!(r.output.contains("timed out"), "{:?}", r);
                assert!(start.elapsed() < Duration::from_secs(10),
                        "killed promptly, took {:?}",
                        start.elapsed());
            }
        }
    }

    #[test]
    fn read_confinement_blocks_outside_files() {
        let dir = jail("read");
        match landlock_abi() {
            None => {
                let err = run_confined(&dir,
                    &["cat".into(), "/etc/hostname".into()], 10)
                    .expect_err("must fail closed");
                assert!(err.contains("Landlock"), "{err}");
            }
            Some(_) => {
                // /etc is readable (system root) - use a path that
                // is NOT under any allowed root: another temp dir.
                let secret = std::env::temp_dir().join(format!(
                    "las-secret-{}", std::process::id()));
                std::fs::write(&secret, "secret").unwrap();
                let r = run_confined(&dir,
                    &["cat".into(),
                      secret.to_string_lossy().into_owned()], 10)
                    .unwrap();
                assert!(!r.ok,
                        "reading outside allowed roots is refused: \
                         {:?}", r);
            }
        }
    }

    #[test]
    fn resolve_command_uses_path_and_refuses_relative() {
        assert!(resolve_command("definitely-not-a-command-xyz")
                .is_none());
        assert!(resolve_command("./relative").is_none());
        if landlock_abi().is_some() || cfg!(target_os = "linux") {
            // touch exists in PATH on CI and here (coreutils).
            assert!(resolve_command("touch").is_some(),
                    "touch should resolve via PATH");
        }
    }
}
