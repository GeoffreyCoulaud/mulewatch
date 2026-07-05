# mulewatch

mulewatch surveille le réseau eMule (eD2k + Kad) en continu pour retrouver des médias perdus.
Sa première mission : le doublage français de *Keroro mission Titar* (Teletoon, 2008),
aujourd'hui en grande partie introuvable.

Ces épisodes ne disparaissent pas tout à fait. Ils réapparaissent par intermittence, le temps
qu'un détenteur se connecte, puis s'éclipsent. Une recherche manuelle tombe presque toujours au
mauvais moment. Une veille permanente et distribuée, non : plusieurs chercheurs font tourner un
nœud, chacun cherche sans relâche, catalogue ce qu'il croise, et alerte dès qu'un épisode
manquant refait surface.

> **Éthique.** Le sujet du catalogue est le fichier, jamais la personne. mulewatch ne piste
> personne et ne cherche à désanonymiser personne : il note qu'un fichier existe, où et quand il
> a été vu, rien de plus.

## Monter un nœud

Compter une quinzaine de minutes. Installer Docker est de loin l'étape la plus difficile : le
reste tient en une commande et deux mots de passe à choisir.

Par défaut, un nœud tourne en mode **observer** : il ne télécharge rien et ne partage rien. Il
cherche, catalogue et notifie. Une fois lancé, un catalogue web est disponible sur
http://localhost:8080 et des tableaux de bord sur http://localhost:3000.

Le pas-à-pas complet (secrets à renseigner, variante derrière VPN, mode téléchargement optionnel)
vit dans le [guide de déploiement](docs/runbooks/deployment.md). En cas de souci, le
[guide de dépannage](docs/runbooks/troubleshooting.md) va du symptôme à la cause à la solution.

## Comment ça marche

Un nœud répète une boucle simple :

1. **Chercher.** Il embarque un client eMule et lance en continu des recherches dérivées de la
   liste des épisodes cibles, sur les deux réseaux d'eMule.
2. **Évaluer.** Chaque fichier vu est confronté à cette liste (titres, numéros, dates de
   diffusion) et reçoit un score de confiance.
3. **Cataloguer.** Tout ce qui est vu est consigné : empreinte de contenu, taille, moment de la
   rencontre.
4. **Alerter.** Quand une cible manquante apparaît, une notification part avec le lien `ed2k://`
   pour la récupérer.
5. **Télécharger (optionnel).** En mode complet, un candidat sûr est téléchargé dans un
   environnement isolé, vérifié, puis catalogué, sans jamais être re-partagé ni exécuté.

Les catalogues de plusieurs nœuds **fusionnent sans conflit** : chaque fichier étant identifié par
son empreinte de contenu, deux chercheurs qui voient le même fichier écrivent la même ligne.

## Statut fonctionnel (juillet 2026)

| Capacité | État | Détails |
|---|---|---|
| Observer (recherche + catalogage + WebUI) | ✅ Stable | Le mode par défaut, éprouvé. |
| Download (téléchargement + vérification) | ⚠️ Construit, pas encore éprouvé en prod réelle | Chaîne complète confirmée par lecture des sources d'amuled ; la validation bout-en-bout reste à faire, voir [admin § Limites connues](docs/runbooks/administration.md#limites-connues--follow-ups). |
| High-ID par port-sync (route A) | ⚠️ Construit, validation bout-en-bout pendante | Exige Docker rootful natif sous Linux. |
| High-ID par port-forward manuel (route B) | ✅ Fonctionnel | Toute plateforme avec un port redirigé sur la box. |
| Antivirus (clamav) | ⚠️ Activé par défaut en download, rlimits non validés | Hypothèse de calibration à éprouver en homelab, voir [admin § clamav](docs/runbooks/administration.md#analyse-antivirus-clamav--provisioning--réglage). |
| Fusion de catalogues (multi-nœuds) | ✅ Outil `merge` disponible | Cycle de partage hors-ligne, voir [docs § Collaboration](docs/README.md#collaboration-entre-chercheurs). |
| Hub central / notifications inter-nœuds | ❌ Non-objectif v0.x | Non prévu. |

## Pour les développeurs

Python ≥ 3.14, workspace `uv`, architecture Clean/Hexagonal, `mypy --strict`, TDD strict (les
tests sont la spec), 100 % de couverture de branches par paquet.

```bash
./scripts/setup-dev.sh   # installe l'environnement (uv sync --dev) et le hook de pré-push
uv run poe check         # le gate complet : lint, types, SQL, tests
```

- Conception : [spec MVP du crawler](docs/specs/2026-06-10-crawler-mvp-design.md)
- Tests : [guide de test](docs/testing-guide.md)
- Déploiement : [guide de déploiement](docs/runbooks/deployment.md)
- Éthique et vie privée : [légalité et confidentialité](docs/legal-and-privacy.md)

---

mulewatch est un outil générique. *Keroro mission Titar* (VF) est sa première mission.
