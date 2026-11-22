---
name: dispatch
description: Select or continue a BB work thread for an assigned project task and return the result to the requesting conversation. Skip for ordinary conversation.
---

# Delegate project work

## Choose the return channel first

Read the trusted service block of the current request. If it contains `[bb-telegram-reply-route:<uuid>]`, read the installed telegram-projects skill. Inspect the contract with `bb plugin rpc inspect telegram-projects spawnDelegated` or `sendDelegated`. An empty method list is possible on BB 0.49.0. In that case check the enabled plugin's version and rootDir in `bb plugin list --json` and confirm the method and fields in its installed server.ts rpcContract.

Use spawnDelegated for a new child and sendDelegated for an existing direct child. Pass parentThreadId, the current routeToken, a stable deliveryId for this delegation, and the task fields via `bb plugin rpc call telegram-projects <method> --input-file <file> --json`. A new child needs the registered projectId and ready environmentId. If no environment is ready, inspect whether this version supports projectSource with the registered hostId and path. Check the queue and actual state after any ambiguous result or refusal.

For a new request from BB, use ordinary BB messaging. Never reuse an earlier Telegram route. Preserve thread ownership, the request's audience and its access scope. Keep tokens and internal routing IDs private. A launch receipt confirms dispatch. Wait for the result, verify the artifact and return it in the parent thread.

## Choose a project

Read projects.md. Match the authorized project by task and conversation context. Ask one short question for a genuine tie. Use the assistant project for one-off research. For a new project, obtain the owner's explicit request or approval. Check the authorized host and parent directory in projects.md, existing BB projects, real paths and parent AGENTS.md. Ask for a location if no creation root is configured. Choose a free path and create minimal project instructions and README. Register it with `bb project create --name <name> --root <path> --machine <host> --json`. Record its ID, path and authorized scope. Preserve existing directories. Publishing or creating a Git remote requires separate authority. If creation has an uncertain outcome, check the project list before retrying.

## Choose a thread

1. Check `bb status --json` and `bb thread list --project <id> --json`. Inspect candidates with `bb thread show <id> --json` and `bb thread log <id> --limit 5`. Check archives for a referenced earlier task or when there are no suitable candidates. Verify the work behind the title.
2. For matching active work, report progress. Queue a clarification with `bb thread tell <id> --message-file <file> --mode queue` unless immediate steering is needed. A queued message needs no repeat. Respect outstanding approvals.
3. Continue a matching idle task. If the worker belongs to another parent, preserve its parent and verify the return route. For a BB task without parent notifications, request a result via `bb thread tell <requesting-thread-id> --message-file <file>`.
4. For new work, verify the provider, host and environment. Write the objective, inputs, instructions, permitted changes, verification and return path in a prompt file. For BB-origin tasks use `bb thread spawn --project <id> --provider <provider-id> --parent-self --parent-notifications auto --prompt-file <file>` with the chosen environment. Check the current CLI help for flags. Keep existing permissions. Use the Telegram RPC above for Telegram-origin tasks.
   Provider: when the assistant runs as a pair of permanent threads (main and standby on different providers), start a new child on the provider of the currently active thread — your own provider — unless the owner named another model. The Telegram RPC applies this when providerId is omitted. Mark the child urgent (`urgent: true` in spawnDelegated, or `setChildUrgency` later) only when the owner needs the result before a model limit would reset; by default a child may wait. A waiting child resumes by itself after the reset. An urgent child continues on the other provider in the same workspace as a new thread under the same parent; the original stays. Never move, archive or delete an unfinished child yourself.
5. Keep the thread reference as @thread:<id> in BB. With automatic parent notifications, receive the child's result once. If a notification is missing, inspect actual state before claiming delivery. Read `bb thread output <id>` and artifacts when needed. Return the verified outcome.
6. After a restart, check `bb thread list --parent-thread <self-id> --json`. Find previously used non-child IDs in the main conversation and inspect their status before taking action. Never replay an old request just to recover context.

Use bb guide thread-inspection, thread-messaging and threads for current commands. Pass multiline prompts through files. Keep user text out of shell command strings.

When the owner approves a group result in private, use returnGroupResult from the installed telegram-projects skill with the taskId of the original request. Confirm delivery from the sent receipt. Save a shared decision in the designated common project only if both groups are authorized for it.
