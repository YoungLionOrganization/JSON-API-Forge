# Editor collaboration and GUI repair

The Editor product version remains 0.5.1. This repair also requires the main
branch's project-scoped capabilities/profile API fix for project-only members.

## Behavior

Disconnect stays in the workspace header when navigation is hidden. Hiding the
sidebar immediately returns its splitter space to the editor. Compact team
docks keep project navigation and document editing available, and tab contents
scroll when their controls cannot fit.

Team actions follow the effective server permissions, including wildcard grants.
Startup skips unauthorized people, role and audit requests. Selecting a project
loads that project's capabilities; late replies from previous selections are
ignored. Session expiry cancels pending requests and preserves unsaved documents.

Chat submission preserves its draft until acknowledgement, prevents duplicate
submissions while pending and retains separate drafts when switching areas.
Messages refresh after successful sends, preserve manual scroll position and
wrap long bodies. Team failures are displayed inside the workspace.

Notes use the server's create-only API. "Share as new note" makes that behavior
explicit; a delayed acknowledgement cannot erase newer edits. Restricted notes
send the chosen minimum reader rank instead of silently sending zero.

Visual properties retain their JSON types. Repeated Visual/Graph selection and
opening another document no longer serialize stale buffers over newer content.
Call windows request microphone/camera access for the exact server origin and
dispose the page before its temporary WebEngine profile.

## Verification

- Native warnings-as-errors build and existing core tests.
- Native GUI regressions for sidebar layout, compact docks, logout revocation,
  editor modes, typed properties, stale responses, role permissions and drafts.
- The real canonical FastAPI server with disposable SQLite state: founder setup,
  scoped member enrollment and full window startup, document validation/save,
  profiles, chat, notes, file upload/download, filtered database rows, audit,
  call tickets and logout.
- GitHub Actions runs GUI tests on all six platforms and under sanitizers. Linux
  x64 also checks the native client against a pinned main-branch server commit.

The local MinGW build has no Qt WebEngine. Physical microphone/camera access and
two-person WebRTC media were not exercised by the local contract test; the test
verifies call creation and ticket URLs. Platform builds compile WebEngine support
where Qt provides it.

Run the contract locally after building with `BUILD_TESTING=ON`:

```sh
python editor/tests/run_server_contract.py --server-source ../main \
  --test-binary build/editor/forge_editor_ui_tests
```

Use the `.exe` suffix and your actual build path on Windows. The Python runtime
must have the main branch's requirements installed.
