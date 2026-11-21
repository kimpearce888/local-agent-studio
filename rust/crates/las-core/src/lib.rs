//! Local Agent Studio core engine (Rust).

pub mod agents;
pub mod communication;
pub mod storage;

pub use agents::{Agent, AgentStatus, ProfileSpec, RetentionPolicy};
pub use communication::{
    BusyMode, DelegationPayload, DeliveryState, MessageKind,
    TransferPayload,
};
pub use storage::CoreStore;
