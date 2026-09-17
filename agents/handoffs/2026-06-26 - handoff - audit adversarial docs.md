# Handoff — emule-indexer (audit adversarial des documentations)

> **Pas de jalon taggé** : session **uniquement docs** (+ 1 yaml change cohérent avec une décision
> doc tranchée). Aucun code de prod modifié, aucun test ajouté ou retiré. Le gate reste vert sans
> intervention (les changements ne le touchent pas).
>
> Aucun tag posé. Un `v0.6.x-doc-adversarial-audit` annoté serait légitime mais pas indispensable :
> le rapport `agents/reference/2026-06-25-doc-adversarial-audit.md` est marqué CLOS, et chaque commit
> a `Adversarial audit 2026-06-25` dans son message (cherchable via `git log --grep`).

## 1. TL;DR — où on en est

L'audit adversarial des 14 docs vivants (lancé le 2026-06-25 sur demande de Geoffrey) est
**résorbé** :

- **114 findings totales** issues de 10 angles adversariaux parallèles (1 subagent `Explore` par
  angle) + 1 critic de complétude, exécutés en `Workflow` sur 11 agents.
- **Triage manuel** par Geoffrey (session 2) : 94 FIX direct, 17 DISCUSS, 2 IGNORE, 1 UNTRIAGED.
- **Session 2bis** (questions de cadrage interactives via `AskUserQuestion`) : 14 décisions
  groupées prises, résultat : 15/17 DISCUSS résolus en FIX, 2 en IGNORE, 1 UNTRIAGED résolu en FIX.
- **Session 3 (cette session)** : **32 commits** appliqués, **110 findings traitées**, **4 ignorées**
  explicitement (2 sur `docs/README.md` d'origine + ed2k:// + clamav-tests-rationale).
- **Nouveautés** : 1 nouveau doc (`docs/legal-and-privacy.md`), 1 nouvelle section
  (`docs/README.md § Collaboration entre chercheurs`), 1 décision tranchée et propagée sur 3 docs
  + 1 yaml (WebUI `:ro` → `PRAGMA query_only=ON` seul).

Le rapport d'audit `agents/reference/2026-06-25-doc-adversarial-audit.md` est marqué **CLOS
(2026-06-26)** ; il reste comme trace (méthodologie, corpus brut, décisions de triage). Le corpus
JSON sibling (`...corpus.json`) est aussi commit-é.

## 2. État vérifiable

```bash
( cd packages/matching && uv run pytest -q )    # inchangé
( cd packages/crawler  && uv run pytest -q )    # inchangé
( cd packages/verifier && uv run pytest -q )    # inchangé
( cd packages/webui    && uv run pytest -q )    # inchangé
uv run ruff check . && uv run ruff format --check . && uv run mypy   # inchangé
git log --oneline 3adf2a9~1..HEAD              # 32 commits docs (+ b0b5faf yaml)
git log --grep "Adversarial audit 2026-06-25" --oneline   # même liste (autre angle)
```

Aucune commande de validation côté **prod** ne tourne dans cette session — c'est délibérément 100 %
documentaire. Le seul fichier non-doc modifié est `bricks/compose.core.yaml` (item 5 du triage :
yaml aligné avec la doc qui acte le retrait de `:ro`).

## 3. Ce qui a été corrigé (32 commits + 1 yaml, regroupés par cible)

| Lot | Cible | Commits | Findings traitées |
|---|---|---:|---:|
| #6 | `docs/runbook-deployment.md` | 7 | 29 |
| #7 | `docs/runbook-administration.md` | 9 | 23 (+ #7 différée vers #14) |
| #8 | `docs/runbook-troubleshooting.md` | 6 | 15 |
| #9 | `README.md` | 1 | 6 |
| #10 | `CLAUDE.md` | 1 (+1 mêlée à #11) | 6 |
| #11 | `docs/testing-guide.md` (+ retouche CLAUDE) | 1 | 6 |
| #12 | `agents/reference/*.md` (4 fichiers) | 4 | 10 |
| #13 | **NEW** `docs/legal-and-privacy.md` | 1 | (3 findings cross-doc absorbées) |
| #14 | **NEW** section `docs/README.md` + xref admin | 1 | 1 (item 2 triage + #7 admin différé) |
| #15 | `bricks/compose.core.yaml` (yaml `:ro` retiré) | 1 | (item 5 triage, alignement doc → yaml) |

Pour le détail commit-par-commit : chaque message de commit liste les findings concernées avec leur
ID d'origine dans le rapport (`Adversarial audit 2026-06-25 : findings #X, #Y, ...`).

## 4. Décisions de conception prises avec Geoffrey (ne pas re-litiger)

Ces 14 décisions ont été prises explicitement en session 2bis via `AskUserQuestion` ; elles sont
toutes appliquées dans les commits ci-dessus.

| # | Sujet | Décision retenue |
|---|---|---|
| 1 | Légalité/confidentialité/éthique | **NEW** `docs/legal-and-privacy.md` (au lieu d'encart inline ou de retrait minimaliste). |
| 2 | Fédération multi-instances | Section dans `docs/README.md` + paragraphe ops dans runbook-administration (au lieu d'un doc dédié séparé). |
| 3 | ed2k:// en 2026 | **IGNORE** — la prémisse de l'agent (« clients morts ») est fausse (aMule 3.0+ viable). |
| 4 | Route A audience | Avertissement prérequis explicite en tête de section (au lieu de déplacer dans un advanced doc). |
| 5 | WebUI `:ro` | **Trancher** : retirer `:ro` partout, `PRAGMA query_only=ON` applicatif suffit. Doc + yaml + ref marquée CLOSED. |
| 6 | clamav rlimits | Encart « hypothèse + symptôme + procédure d'ajustement » dans runbook-admin. |
| 7 | amuled hardening status | Aligner sur « non-objectif assumé pour v0.x » (cohérent CLAUDE.md, écrasement des contradictions dans troubleshooting et l'audit du 23). |
| 8 | Acquisition clé WireGuard | Paragraphe générique + lien wiki gluetun (au lieu d'un guide détaillé par fournisseur qui rote). |
| 9 | Liste fournisseurs VPN | Lien dynamique vers la liste gluetun (au lieu d'une liste en dur). |
| 10 | 4 contraintes mode download | Source unique = `reference/2026-06-17-amuled-completion-behavior.md`, runbooks pointent dessus (au lieu de garder la duplication × 4 docs). |
| 11 | ffmpeg/ffprobe install testing-guide | Mention sobre + lien `ffmpeg.org/download` (au lieu de commandes par OS). |
| 12 | clamav tests rationale testing-guide | **IGNORE** — pas pousser de procédure non-testée dans testing-guide. |
| 13 | testing-guide audience | Corriger L9 en « Dev / CI » + aligner L88 (au lieu de réécrire). |
| 14 | UNTRIAGED ec-download-opcodes PENDING | FIX — même statut DEFERRED daté que son jumeau ec-field-richness. |

## 5. Mécanique du workflow adversarial (pour réutiliser)

Le workflow a été lancé via `Workflow` tool (opt-in multi-agent confirmé par Geoffrey), avec
**11 agents en parallèle** (10 angles + 1 critic), tous `subagent_type: 'Explore'` (read-only),
schéma JSON forcé pour permettre dédup mécanique.

**Script de référence** : `/Users/geoffrey/.claude/projects/.../workflows/scripts/doc-adversarial-audit-2026-06-25-*.js`
(persisté par le runtime). Réutilisable via `Workflow({scriptPath: ..., resumeFromRunId: ...})`.

**Coût observé** : ~920 k tokens subagent, ~9 min wall-clock pour le run principal + 1 standalone
`Agent` pour relancer un angle qui avait crashé en `API Error: Overloaded` (l'angle iii Tribal
knowledge). Le run standalone a produit 18 findings cohérentes avec les 9 autres.

**Critic de complétude** (1 agent supplémentaire, lit le corpus + les docs) a identifié 5 angles
manquants — utiles pour un futur audit :
- *First-boot success validation* (« comment je sais que ça marche ? »)
- *Multi-instance federation* (déjà traité ici dans le lot #14)
- *Operational health indicators / KPI baselines*
- *Data lifecycle / backup and disaster recovery*
- *Dependency lifecycle / version management strategy*

Ces 5 angles **n'ont PAS été ajoutés** à l'audit (qui est CLOS) mais sont consignés pour un audit
ultérieur. Le KPI baseline + backup/disaster recovery sont les plus critiques pour la maturation
opérationnelle du projet.

## 6. Liens vivants ajoutés ou consolidés (vérifier en cas de refonte ultérieure)

- `runbook-administration § Route B "À savoir"` → `legal-and-privacy.md` (créé)
- `runbook-administration § WebUI` → `reference/2026-06-22-webui-wal-readonly.md` (marqué CLOSED)
- `runbook-deployment § Étape 3` → `reference/2026-06-17-amuled-completion-behavior.md#contraintes-de-déploiement-résumé` (source unique pour les 4 contraintes)
- `runbook-deployment § Étape 5/6` → ancres internes au document (Premier boot, En cas d'erreur)
- `docs/README` → `legal-and-privacy.md`, `runbook-administration#outils-de-catalogue`, etc.
- `README.md` → `docs/legal-and-privacy.md`, `docs/runbook-troubleshooting.md` (manquait)
- `runbook-administration § Outils de catalogue` → `docs/README#collaboration-entre-chercheurs`
- `CLAUDE.md § Confinement posture` → ajoute le rationale inline (auto-suffisant pour Claude)

## 7. Prochaine étape recommandée

1. **Relire les nouveautés à tête reposée** : `docs/legal-and-privacy.md` et la section
   « Collaboration » de `docs/README.md`. Ce sont les contenus les plus opinionated — vérifier
   qu'ils correspondent à ta voix éditoriale et à la stratégie projet.
2. **Tester un déploiement réel** (homelab) en suivant le runbook révisé : c'est l'épreuve du
   feu pour les fixes de tribal knowledge / faux amis / omissions. Tu découvriras les manques
   résiduels que l'audit n'a pas vus parce qu'il était lecture statique.
3. **(Optionnel) `git tag -a v0.6.x-doc-adversarial-audit -m "..."`** si tu veux une trace de
   milestone. Pas indispensable, comme noté en tête.
4. **(Optionnel) Audit suivant** sur l'un des 5 angles manquants identifiés par le critic — KPI
   baselines et backup/disaster recovery sont les candidats prioritaires.

## 8. Ce qui n'a PAS été touché (rappel)

- `agents/handoffs/*` — par convention (archives par milestone).
- `docs/superpowers/specs/*` et `docs/superpowers/plans/*` — par convention (specs figées).
- Tous les fichiers de code (`packages/*/src/`, tests, configs YAML autres que `compose.core.yaml`).
- Le gate (ruff/format/mypy/pytest) — pas exercé cette session, mais inchangé par construction.
- `.pytest_cache/README.md`, `.superpowers/sdd/*` — artefacts, hors scope.

## 9. Mémoire mise à jour

Une nouvelle feedback memory a été ajoutée : `feedback_no_pre_render_diff.md` — Geoffrey préfère
appliquer les diffs directement (Edit + commit) plutôt que les pré-rendre en markdown pour
validation. Validation seulement pour cas critiques (structurels, destructifs, hors-scope).
