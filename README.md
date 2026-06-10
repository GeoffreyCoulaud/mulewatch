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

Le mode **observer** ne télécharge rien : il cherche, catalogue et notifie (avec un lien
`ed2k://` pour récupérer un fichier d'un clic). Portable (Linux, macOS, Windows via Docker
Desktop), sans configuration réseau particulière.

> ⚙️ Le packaging `docker compose` arrive dans un incrément ultérieur (voir la feuille de route).
> Le projet en est aux **fondations** (voir « Pour les développeurs »).

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
./scripts/setup-dev.sh   # active les hooks Git (core.hooksPath) + installe l'env (uv sync)
uv run pytest
```

> Les **hooks Git ne sont pas activés automatiquement au clone** (sécurité Git) : `setup-dev.sh`
> règle `core.hooksPath=.githooks`. Le hook **pré-push** rejoue les checks de la CI (ruff,
> format, mypy, pytest) — un push ne part pas si un check échoue.

### Conception
- Spec : [`docs/superpowers/specs/2026-06-10-crawler-mvp-design.md`](docs/superpowers/specs/2026-06-10-crawler-mvp-design.md)
- Plans d'implémentation : [`docs/superpowers/plans/`](docs/superpowers/plans/)

## Statut

🚧 En construction — fondations posées (toolchain, CI, normalisation). Feuille de route
détaillée dans la spec et les plans.
