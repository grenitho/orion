const BASE_URL = 'http://127.0.0.1:8000';

export class BackendService {
    async setProject(path: string): Promise<any> {
        const res = await fetch(`${BASE_URL}/set-project`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path })
        });
        return res.json();
    }

    async chat(message: string): Promise<any> {
        const res = await fetch(`${BASE_URL}/chat`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message })
        });
        return res.json();
    }
}