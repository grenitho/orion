import { orionHeaders } from './authToken';
import { ensureBackendRunning } from './backendManager';

const BASE_URL = 'http://127.0.0.1:8000';

export class BackendService {
    async healthCheck(timeoutMs = 1500): Promise<boolean> {
        const ctrl = new AbortController();
        const timer = setTimeout(() => ctrl.abort(), timeoutMs);
        try {
            const res = await fetch(`${BASE_URL}/health`, {
                headers: orionHeaders(),
                signal: ctrl.signal,
            });
            return res.ok;
        } catch {
            return false;
        } finally {
            clearTimeout(timer);
        }
    }

    async ensureConnected(projectPath?: string, sessionId = 'default'): Promise<boolean> {
        const ok = await ensureBackendRunning();
        if (ok && projectPath) {
            await this.setProject(projectPath, sessionId);
        }
        return ok;
    }

    async setProject(path: string, sessionId = 'default'): Promise<unknown> {
        const res = await fetch(`${BASE_URL}/set-project`, {
            method: 'POST',
            headers: orionHeaders(),
            body: JSON.stringify({ path, session_id: sessionId })
        });
        return res.json();
    }

    async chat(message: string, projectPath?: string, sessionId?: string): Promise<unknown> {
        const res = await fetch(`${BASE_URL}/chat`, {
            method: 'POST',
            headers: orionHeaders(),
            body: JSON.stringify({
                message,
                project_path: projectPath || undefined,
                session_id: sessionId || undefined,
            })
        });
        return res.json();
    }
}
