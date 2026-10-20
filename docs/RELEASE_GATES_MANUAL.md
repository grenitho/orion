# RELEASE GATES — manual VS Code scenarios (FASE_C_PLAN §6 c & d)

Two of the four manual acceptance scenarios need human eyes inside VS Code:
**c** (broken file → `verify_code` cited with line numbers) and **d** (Cancel
mid-generation → clean cancel). Their **server-side halves are already
evidenced** by the deterministic mock harness described in FASE_C_PLAN §5:

| Gate | Server-side evidence (mock harness, kept outside the repo) |
|---|---|
| c | scenario h: stream = 4×status (`Thinking`, `Processing tool`, `🩺 Verifying: src/broken.py`, `Thinking`) + token + done; the tool result handed to the model contains `PYTHON SYNTAX line 4: '(' was never closed` plus the quoted source line — the exact text a compliant report cites |
| d | cancel test: client aborted after the first `status` event; collected events contained **no `error` and no `done`**; backend health probe 200 immediately after; the next chat produced the reference sequence byte-identical; backend log showed **no** `Traceback` / `Task was destroyed` / `Unhandled` lines |

What remains is the **client-side half** below. Run both before tagging a
release; each takes about two minutes.

---

## Scenario c — broken file, quoted findings

1. In the project Orion works on, create `broken_demo.py`:

   ```python
   def greet(name):
       msg = "hello " + name
       if len(msg) > 0:
           print(msg
   ```

   (missing closing parenthesis on line 4)
2. In the Orion chat, send: *"Fix broken_demo.py and report what was wrong."*
3. **Expect**
   - Activity view shows `🩺 Verifying: broken_demo.py` (Orion called
     `verify_code`, it did not guess);
   - the final report **quotes the finding with the line number**
     (`PYTHON SYNTAX line 4 ...` and/or the `print(msg` snippet);
   - a checkpoint question (`ask_user`) closes the turn;
   - if the project is a git repo, a `git_commit` checkpoint is offered/done —
     and **no** attempt to `git push` (that is your manual action).
4. **Fail signs**: a completion claim without any quoted finding; a
   `Verifying completion claim (0 files changed)` challenge *after* a real
   edit (that regression is guarded by baseline scenario g); any
   `[REJECTED BY SYSTEM]` for a git command Orion ran on its own initiative.

## Scenario d — Cancel mid-generation

1. Send a deliberately long task, e.g. *"Audit every file in this project and
   summarize each one."*
2. While the activity view is streaming (`Thinking...`, tool labels), press
   **Cancel** in the chat panel.
3. **Expect**
   - a `🛑 Cancelled by the user.` bubble and a `Cancelled` activity entry;
   - the send button re-enabled (`Send`), input editable immediately;
   - **no** red/`⚠️ Error` bubble and no "connection lost" message;
   - the backend stays healthy: the next chat answers normally;
     `.orion/logs/backend.log` shows no traceback for the cancelled run.
4. **Fail signs**: stuck spinner, disabled input, phantom error bubble, or a
   backend that needs a restart afterwards.

---

## Sign-off

- [ ] scenario c passed (findings quoted with line numbers)
- [ ] scenario d passed (clean cancel, healthy backend)
- [ ] `python -m pytest backend/app/services/ -q` green on the release commit
- [ ] five-to-seven mock SSE baselines re-recorded byte-identical (harness owner)

Only tick the boxes on the commit you intend to tag.
