# emule-indexer

Retrouver le *lost media* **Keroro mission Titar (VF)** en surveillant eMule en continu,
et cataloguer un maximum de métadonnées au passage.

## Pourquoi ce projet

Une grande partie du doublage français de *Keroro mission Titar* (diffusé sur Teletoon en 2008)
est perdue. Les épisodes réapparaissent **par intermittence** sur le réseau eMule, quand un
détenteur se connecte ; une recherche manuelle ponctuelle les rate presque toujours.
**emule-indexer** transforme ce hasard en **surveillance permanente et distribuée** : plusieurs
chercheurs font tourner un nœud, chacun cherche en continu, catalogue ce qu'il voit, et alerte
quand un épisode manquant apparaît.

> Éthique : le sujet du catalogue est **le fichier**, pas la personne. Pas de pistage ni de
> désanonymisation — uniquement retrouver des épisodes perdus.

## Pour les chercheurs (faire tourner un nœud)

Le mode **observer** ne téléchargera rien : il cherchera, cataloguera et notifiera (avec un lien
`ed2k://` à ouvrir avec aMule pour récupérer un fichier). Portable sur Linux, macOS, Windows (via
Docker Desktop) en mode **observer** ; le mode **High-ID** (optimisation des sources) exige Docker
rootful natif sur **Linux**.

Quatre stacks de déploiement disponibles selon votre besoin :
- **A** observer derrière VPN (recommandé pour la confidentialité, demande un abonnement VPN avec
  WireGuard ≈ 2-5 €/mois) ;
- **B** download + High-ID derrière VPN (demande un VPN à port forwarding) ;
- **C** observer sans VPN (votre IP domestique est visible des pairs eMule) ;
- **D** download + High-ID sans VPN, port ouvert sur la box (votre IP domestique exposée).

Pour la confidentialité, **préférez A ou B**. Détails et matrice complète dans le
[runbook de déploiement](docs/runbooks/deployment.md) ; les implications légales et de
confidentialité sont discutées franchement dans
[légalité et confidentialité](docs/legal-and-privacy.md).

> 🐳 Déploiement `docker compose` (profils `observer`/`download`, option `monitoring`)
> disponible — voir [`docs/runbooks/deployment.md`](docs/runbooks/deployment.md). Pour résoudre un
> problème : [`docs/runbooks/troubleshooting.md`](docs/runbooks/troubleshooting.md).

## Pour les curieux (comment ça marche)

1. Le nœud parle le protocole eMule (eD2k + Kad) via un client aMule piloté en interne.
2. Il lance en continu des recherches dérivées d'une liste d'épisodes cibles.
3. Chaque résultat est **scoré** contre cette liste (titres, numéros, dates de diffusion…).
4. Selon la confiance : on catalogue, on notifie, ou (mode complet) on télécharge dans un
   environnement **isolé**, sans jamais re-partager ni exécuter le contenu.
5. Les catalogues de plusieurs chercheurs **fusionnent** sans conflit (chaque fichier est
   identifié par son empreinte de contenu).

## Pour les développeurs

- **Stack** : Python (`uv`), architecture Clean/Hexagonal, `mypy --strict`, `ruff`, `pytest`.
- **TDD strict** : les tests sont la spec ; aucun code de prod avant les tests ; **coverage 100 %
  imposé** (branch).

### Démarrer
```bash
git clone <repo> && cd emule-indexer
./scripts/setup-dev.sh   # active les hooks Git (core.hooksPath) + installe l'env (uv sync --dev)
( cd packages/matching && uv run pytest -q )
( cd packages/crawler  && uv run pytest -q )
( cd packages/verifier && uv run pytest -q )
( cd packages/webui    && uv run pytest -q )
```

> Les **hooks Git ne sont pas activés automatiquement au clone** (sécurité Git) : `setup-dev.sh`
> règle `core.hooksPath=.githooks`. Le hook **pré-push** rejoue les checks de la CI (ruff,
> format, mypy, pytest) — un push ne part pas si un check échoue.

### Conception
- Spec : [`docs/specs/2026-06-10-crawler-mvp-design.md`](docs/specs/2026-06-10-crawler-mvp-design.md)
- Plans d'implémentation : [`docs/plans/`](docs/plans/)
- Déploiement (Docker / compose) : [`docs/runbooks/deployment.md`](docs/runbooks/deployment.md)

## Statut fonctionnel (juin 2026)

| Capacité | État | Détails |
|---|---|---|
| Observer (recherche + catalogage + WebUI) | ✅ Stable | Sur les 4 stacks. |
| Download (téléchargement + vérification) | ⚠️ Fonctionnel, non éprouvé en prod réelle | Chaîne complète confirmée par lecture des sources amont d'amuled ; la suite e2e bout-en-bout a été abandonnée, voir [admin § Limites connues](docs/runbooks/administration.md#limites-connues--follow-ups). |
| High-ID via port-sync (Route A) | ⚠️ Construit, validation bout-en-bout pendante | Exige Docker rootful Linux. Cf. runbook-admin. |
| High-ID via port-forward manuel (Route B) | ✅ Fonctionnel | N'importe quelle plateforme avec port redirigé. |
| Antivirus (clamav) | ⚠️ Activé par défaut en download ; rlimits non validés en prod | Hypothèse de calibration à éprouver en homelab, voir [admin § clamav](docs/runbooks/administration.md#analyse-antivirus-clamav--provisioning--réglage). |
| Multi-instances / fusion catalogues | ✅ Outil `merge` disponible | Cycle de partage hors-ligne, voir [docs/README § Collaboration](docs/README.md#collaboration-entre-chercheurs). |
| Hub central / notifications inter-nœuds | ❌ Non-objectif v0.x | Non prévu. |
