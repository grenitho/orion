# Starting the backend

Normally you never think about this: when VS Code opens, Orion looks for the
backend folder and starts it automatically. You will see the notification
**"Orion: Backend started automatically."**

## When it does not start

1. Run **Orion: Start Backend** from the Command Palette
   (`Ctrl+Shift+P`) — it retries discovery and startup.
2. If it reports *backend folder not found*, set the path manually:
   **Settings → `orion.backendPath`** → e.g. `C:\Users\you\orion\backend`.
3. If it reports *virtual environment not found*, run the installer again or
   follow the setup guide — it takes care of Python, `venv` and
   dependencies for you.

## Check that it is alive

Open <http://127.0.0.1:8000/> in your browser — a healthy backend answers
with a small JSON status page. The backend only listens on your own
computer (loopback); it is not reachable from the network.
