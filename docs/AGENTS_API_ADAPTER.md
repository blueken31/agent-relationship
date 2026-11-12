# OpenAI Agents API observation adapter

This adapter connects the SDK's observation boundary to the documented **OpenAI
Agents API (managed Codex harness)** event stream. It does not implement standalone
Codex CLI JSONL compatibility. Its first validation is a **docs-derived synthetic
replay**, not a recorded live session or a measured semantic improvement.

The integration has two explicit steps:

1. `normalize(event)` selects provider event identity and coordination metadata.
   It is pure, does not call models or change the tracker, and leaves task outcome
   and assessment unknown.
2. `track(tracker, event, binding=..., context=...)` records a relevant interaction
   after the host supplies the original task, both agent identities, direction,
   shared goal, expectations and selected evidence. This calls the tracker's
   configured assessment backend and can therefore incur model cost.

The host still owns streams, credentials, saved-history retrieval, verification,
authorization, execution, and reconciliation. A provider event is not proof of an
interaction's effect on a user goal.

## Supported boundary

| Provider event/item | Adapter treatment |
| --- | --- |
| `agent.session.turn.item.done` containing `agent_message` or `send_subagent_input_call` | `needs_context`; eligible for explicit host-bound recording |
| Completed create, wait or interrupt coordination items | `observation_only`; never task-success evidence |
| Subagent created, active or closed | Preserve subagent and parent identity; observation only |
| Turn completed, failed or cancelled | Preserve turn/actor identity; observation only; no pair harm or task-success inference |
| Text deltas, added items, ordinary output messages, unknown future events | Ignored for relation scoring |

The adapter checks the configured session against documented envelope and nested
identities; conflicting turn identities fail before any SDK write. It deliberately
does not infer participants from display names or assume that `agent_id` on a
create item identifies the created child. The documented field identifies the
requesting agent. For message/send items, provider `sender_agent_id` and `recipient_agent_id`
are retained separately when present. Message references may be IDs or names; the
host must resolve these to its canonical agents and preserve their direction.
Host-supplied bindings supply that canonical direction. Missing provider references
stay null; the host must retrieve sufficient saved history before binding. There
is no automatic roster or task mapping.

## Attach to an existing host

The following handler fits an existing authenticated Agents API stream. It does
not create a session or prescribe credentials/model selection. `host_lookup` is
application code: it must return an authoritative mapping and reviewed evidence,
not an LLM-generated assertion of success.

```python
from agent_relationship.adapters import AgentsAPIAdapter, HostBinding

adapter = AgentsAPIAdapter(saved_session_id)

# client and tracker are already configured by the host.
with client.beta.agents.sessions.events.stream(saved_session_id) as stream:
    for event in stream:
        raw = event.model_dump(mode="json")
        observation = adapter.normalize(raw)
        if observation["disposition"] != "needs_context":
            continue
        evidence = host_lookup(observation)  # Host function; may return None.
        if evidence is None:
            # Keep it in the host's unassessed observation log, without
            # creating a scored relation or guessing a pair from metadata.
            continue
        result = adapter.track(
            tracker, raw,
            binding=HostBinding(
                task_id=evidence.task_id,
                initiator=evidence.initiator_id,
                target=evidence.target_id,
                evidence_source=evidence.verifier_name,
            ),
            context={
                "action": "handoff",
                "goal": evidence.shared_goal,
                "expected_result": evidence.expected_result,
                "narrative": evidence.summary,
                "observations": evidence.selected_observations,
                "evidence_refs": evidence.reference_ids,
                "result": evidence.observed_result,
            },
        )
        # Display tracker.task_view(evidence.task_id) through the host UI.
```

The snippet only shows an observation handler; the host must handle root-turn
termination, failure, disconnects, stream retry and retrieval of saved items as
specified in the official documentation. Coordination items can omit message
content, and the stream is not a complete transcript. Do not replace missing
history with invented facts.

Required context fields are `action`, `goal`, `expected_result`, `narrative`, a
nonempty string list `observations`, and a nonempty string list `evidence_refs`.
`result` is optional and defaults to `unknown`; it is always host-supplied, never
inherited from an item/turn status. Optional `roles`, `dependencies` and
`third_party_effects` preserve host-selected JSON context for assessment; they do
not implement a causal dependency graph or collective-behavior detector.

The context field allowlist excludes `event_id`, `task_id`, mock `scenario`,
`provenance`, `repair_plan_id`, `verification_passed` and `host_authorized`. The
adapter cannot authorize execution or close a repair. A host that independently
verifies a repair records that evidence through the existing recovery workflow.
The `evidence_source` label records provenance; it is not cryptographic attestation.

## Identity, replay and privacy

Tracker event IDs use a SHA-256 digest of the JSON-encoded `[session_id, event_id]`
pair; delimiters inside either ID cannot collide. The tracker enforces one source
event, one task and one ordered pair. Replaying identical evidence after a SQLite
reopen returns the existing result without another assessment. Rebinding that
source event to another task/pair or changing its host evidence raises. This
initial adapter does not fan a single provider event out into multiple scored
pairs, reconcile distinct provider event IDs for one item, or support corrections.
Hosts must reconcile those cases before recording and retain stable event envelopes.

Normalization keeps no event-order buffer. The tracker scores distinct events in
ingestion order; the host must retrieve and reconcile saved history before replaying
out-of-order observations. A successful duplicate retry does not establish that all
earlier events were received.

Only selected provider metadata and a payload digest are retained in the tracker.
Raw provider message text, instructions, tool arguments, errors and random stream
obfuscation are omitted. The digest excludes top-level obfuscation so changing
padding does not defeat deduplication; other changes to a reused source event are
detected. A digest is **not anonymization**. Hosts must still redact sensitive
material in explicitly supplied evidence and apply their retention policy. Input
JSON and returned dictionaries are detached; normalization keeps no mutable cache.

## Reproduce the current validation

```bash
PYTHONPATH=. python -m unittest agent_relationship.tests.test_agents_api_adapter -v
PYTHONPATH=. python examples/agents_api_replay.py
```

Tests exercise source/session identity, changed evidence and payload collisions,
SQLite restart deduplication, direction/provenance, defensive copies, missing
context, future events, lifecycle failures and forbidden authorization fields. The
example uses a mock evaluator only to check the plumbing; it does not establish
real model accuracy or a benefit over native harness hooks.

Before claiming live integration: capture a permitted session with at least two
agents, redact and retain a representative event recording, verify provider schema
and host binding, simulate reconnect/replay, inspect each task card against the
actual host evidence, and run the native-harness versus SDK comparison. Current
fixtures are abbreviated synthetic envelopes and are not full provider schema
conformance tests. No live API call or task execution was performed for this batch.

## Official schema sources

Fields and event semantics checked on **2026-11-12 UTC** against:

- [Multi-agent orchestration and observation](https://developers.openai.com/api/docs/guides/agents-api/multi-agent)
- [Session events, saved items and disconnected-stream recovery](https://developers.openai.com/api/docs/guides/agents-api/sessions/events)
- [Agents streaming events reference](https://developers.openai.com/api/reference/resources/beta/subresources/agents/streaming-events)
- [Turn item schema and sender/recipient fields](https://developers.openai.com/api/reference/resources/beta/subresources/agents/subresources/sessions/subresources/turns/subresources/items/methods/list)

The provider API is beta; `adapter_version=1` describes this mapping contract, not a
provider compatibility guarantee. Unknown types stay ignored until reviewed.
