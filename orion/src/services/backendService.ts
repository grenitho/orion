import { orionHeaders } from './authToken';

const BASE_URL = 'http://127.0.0.1:8000';

export class BackendService {
    async setProject(path: string): Promise<unknown> {
        const res = await fetch(`${BASE_URL}/set-project`, {
            method: 'POST',
            headers: orionHeaders(),
            body: JSON.stringify({ path })
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
