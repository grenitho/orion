import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';

// Mirror of backend/app/services/auth.py: same env-var override and the same
// default file in the user's home directory. Read per request, so a freshly
// minted secret (backend restarted, file recreated) is picked up immediately
// without reloading VS Code.
const TOKEN_FILE = process.env.ORION_TOKEN_FILE
    ? path.resolve(process.env.ORION_TOKEN_FILE)
    : path.join(os.homedir(), '.orion_shared_secret');

export function orionHeaders(): Record<string, string> {
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };

    const fromEnv = (process.env.ORION_SHARED_TOKEN || '').trim();
    if (fromEnv) {
        headers['X-Orion-Token'] = fromEnv;
        return headers;
    }

    try {
        const tok = fs.readFileSync(TOKEN_FILE, 'utf-8').trim();
        if (tok) {
            headers['X-Orion-Token'] = tok;
        }
    } catch {
        // Backend has not minted the secret yet (first start still running)
        // or the file is unreadable: the request will 401 and surface in the
        // UI/log instead of crashing the extension host.
    }
    return headers;
}
