# Handoff — 2026-06-29 — simplification du déploiement

Milestone **`v0.20.0-deploy-simplification`** (annoté, non poussé). Mergé dans `main` en `--no-ff`
(`6a7472e`). Gate complet **vert** (matching 185 / crawler 721 / verifier 176 / webui 97, tous
100 % branch ; ruff / format / mypy / sqlfluff / check_templates). Revue globale (opus) :
**Ready to merge**, 0 Critical / 0 Important.

## État courant

Déployer un nœud, c'est désormais : **remplir un seul `deploy/.env` (secrets) → une commande.**
Origine : un essai de déploiement réel a rendu tangible que l'ancien parcours (3 fichiers à
copier/éditer, double-saisie du mot de passe EC, runbook de 382 lignes) était trop lourd. Design
validé dans `docs/specs/2026-06-29-simplification-deploiement-design.md` (décisions D1–D9), plan dans
`docs/plans/2026-06-29-simplification-deploiement.md`.

Le modèle :
- **Secrets → `.env`** (interpolés dans le yaml via `${VAR}`). **Config → yaml** (en dur, éditable).
- Lancement : `docker compose -f deploy/direct.compose.yml [--profile download|monitoring|webui] up
  -d` (sans VPN, défaut) ou `deploy/gluetun.compose.yml` (VPN, masque l'IP). High-ID optionnel
  (ouverture de port `LISTEN_PORT`, ou VPN à port forwarding).

## Ce qui a été construit

| Domaine | Changement |
|---|---|
| Interpolation | `adapters/config/interpolation.py` — `${NAME}` **paresseuse** (à la consommation), **sous-chaîne** (`discord://${ID}/${TOKEN}`), **fail-fast** ; `errors.py` isole `ConfigError` (casse un cycle d'import) |
| Config crawler | `crawler_config.py` parseur **unifié** (fusion de `local_config.py`, supprimé). Sections `download`/`port_sync` **présentes ⟺ `enabled: true`** ; le parseur garantit la complétude → les checks « solidaires » de `app.py` disparaissent |
| Composition | mode déclenché par `config.download is not None` (plus par `verifier_url`). `_require_full_config`/`_require_port_sync_config` supprimés. Health-check verifier fail-fast préservé |
| CLI | `--config` unique (remplace `--crawler`/`--local`) ; `os.environ` passé au parseur ; `validate-config` valide aussi la présence des vars d'env des sections actives |
| `deploy/` | `base.compose.yml` (fragment, service `crawler` **unique**) inclus par `gluetun.compose.yml` / `direct.compose.yml` (nommage `*.compose.yml`, `include:`). `examples/` supprimé. `crawler.yml` unifié versionné ; `.yml` partout. `.env.example` → `deploy/.env.example` (secrets only). Profils `download`/`webui`/`monitoring` |
| Smoke | `tests/smoke/` réaligné : `crawler.yml` (download) + `crawler.observer.yml`, `--config`, `.yml` |
| Docs | `deployment.md` quickstart **382 → 130** ; `administration`/`troubleshooting`/`testing-guide`/`CLAUDE.md` alignés ; specs datées **annotées** (pas réécrites) ; handoffs intouchés |

## Pièges appris

- **Le refactor config est ATOMIQUE.** Changer la signature/retour de `parse_crawler_config` +
  supprimer `local_config.py` casse `app.py`/`__main__.py` jusqu'à leur réécriture. Plan T2/T3/T4
  fusionnés en un seul commit (`9545e67`) — sinon commits intermédiaires rouges. À retenir pour tout
  refactor d'une fonction et de ses appelants.
- **`mypy --strict` se lance depuis la RACINE** (`uv run mypy`), pas depuis `packages/crawler` (qui
  déclenche un faux « Duplicate module __main__ »). Pareil, mypy vérifie AUSSI les tests d'intégration
  désélectionnés (un `test_crawler_loop.py` important `LocalConfig` a dû être migré).
- **80 colonnes même sur les healthchecks compose** : un `python -c "…"` mono-ligne se découpe en
  block scalar YAML `- |` multiligne (sémantique préservée). Le « unsplittable » est faux.
- **`ConfigError` ré-exporté en `as ConfigError`** (PEP 484, `no_implicit_reexport` sous strict).
- **Invariant §3 révisé** : « la config crawler n'utilise aucune variable d'env » devient
  « interpolation `${NAME}` dans l'adapter de config » — le domaine reste pur (l'I/O env vit dans
  l'adapter). Mis à jour dans `CLAUDE.md` et annoté dans la spec MVP.
- **Paresse > YAGNI ici** (décision consciente de Geoffrey) : interpoler à la consommation permet
  qu'une section `enabled: false` n'exige jamais ses `${VAR}` — débloque les itérations futures.

## Prochaine étape suggérée

1. **Valider sur Docker réel** (voir ci-dessous) — rien n'a tourné en conteneurs.
2. Findings non bloquants laissés de côté (revue globale), à reprendre si l'occasion :
   - ajouter `test_port_sync_enabled_true_requires_urls` (symétrie avec le download ; 100 % branch
     déjà atteint, donc optionnel) ;
   - `administration.md` répète `gluetun.compose.yml` dans ~9 exemples avec un seul rappel
     « remplacez par `direct` » (trade-off doc accepté) ;
   - `WEBUI_PORT` pourrait être ajouté en commentaire à `deploy/.env.example` (défaut `:-8080`).
3. Le vrai bloqueur 🔴 du projet reste **`server.met`/`nodes.dat`** (bootstrap eD2k/Kad) et le
   premier vrai cycle download→verify — orthogonaux à ce chantier.

## NON validé sur vrai matériel

- **Aucune stack n'a tourné en conteneurs.** Seul `docker compose config` (résolution/validation
  statique) a été exercé — `DIRECT_OK`/`GLUETUN_OK` confirment que les `include:` résolvent et que
  les services fusionnent, **pas** qu'un nœud démarre et catalogue.
- **Smoke live** : `-m compose_integration` (lance des conteneurs) **n'a pas tourné** (le sandbox n'a
  pas `CAP_NET_ADMIN`/veth). **À lancer par Geoffrey** dans un shell réel pour valider le câblage
  bout-en-bout sous la nouvelle config unifiée.
- **Interpolation `${NAME}` en conditions réelles** : testée à 100 % en unit, jamais exercée via un
  vrai `.env` + `docker compose up` (amuled qui reçoit le mot de passe interpolé).
- Le **port-sync High-ID** et la **stack gluetun** restent non validés empiriquement (dette
  héritée, inchangée par ce chantier).
