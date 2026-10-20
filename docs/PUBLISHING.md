# Publishing Orion to the VS Code Marketplace

Owner runbook. Everything mechanical is checked by CI-grade commands below;
everything that needs a human account is flagged MANUAL.

## One-time setup (MANUAL, ~10 minutes)

1. **Azure DevOps org** — create one at https://dev.azure.com (any name,
   e.g. `grenitho`). A personal account is enough.
2. **Personal access token** — User settings → Security → Personal access
   tokens → New token. Organization: the org from step 1. Scopes:
   **Marketplace → Manage** (publish). Copy it once; never store it in the
   repo or in `.env`.
3. **Create the publisher** — https://marketplace.visualstudio.com/manage →
   *Create publisher*. The ID must match `package.json` (`grenitho`); the
   display name is your brand.
4. **Login once** from the repo root:
   ```bash
   cd orion
   npx vsce login grenitho      # paste the PAT when prompted
   ```

## Pre-publish checklist (per release)

- [ ] `main` is the release commit; `python -m pytest backend/app/services/ -q` green; `npm run compile` clean
- [ ] version bumped in `orion/package.json` and `orion/CHANGELOG.md` has the entry
- [ ] `npx vsce package` → install that VSIX locally and smoke-test one chat
- [ ] `npx vsce ls` shows no secrets (`.vscodeignore` keeps `.env`, `.orion/`,
      `src/`, `node_modules/`, `tools/` out — re-verify when adding files)
- [ ] icon present: `resources/orion-icon.png` (256 px, dark background)

## Publish

```bash
cd orion
npx vsce publish minor     # or patch/major; uploads and bumps in one step
```

Marketplace propagation takes minutes. Verify at
`https://marketplace.visualstudio.com/items?itemName=grenitho.orion` and by
installing from the Marketplace in a clean VS Code profile.

## GitHub Release (mirror for non-Marketplace users)

```bash
git tag v<version> && git push origin v<version>
```
Then GitHub → Releases → *Draft a release from tag* → attach
`orion-<version>.vsix` → paste the CHANGELOG entry as release notes.

## Rollback / republish

Marketplace versions are immutable: a bad upload cannot be overwritten, only
hidden (unpublish) or superseded by a newer version. That is why the checklist
installs and smoke-tests the exact VSIX before publishing.

## Icon regeneration

`resources/orion-icon.png` is a faithful raster of `resources/orion-icon.svg`
(white delta ring on the gallery-banner colour), rendered with 4x
supersampling by `orion/tools/make-icon.py` (pure stdlib):

```bash
python3 tools/make-icon.py 256 resources/orion-icon.png
```

Keep ≥128 px and keep the dark background so the mark reads on light
Marketplace pages.
