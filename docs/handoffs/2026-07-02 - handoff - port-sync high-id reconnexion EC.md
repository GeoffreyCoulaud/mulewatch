# Handoff — Port-sync High-ID : churn WireGuard résolu + reconnexion EC

> Branche `fix/port-sync-ec-reconnect` (in-place, **mergée en fast-forward dans `main`**).
> Pas de spec/plan dédiés — bugfix issu d'une session de debug terrain (déploiement gluetun +
> port forwarding qui restait en Low-ID). TDD strict (test rouge → fix minimal).
> **Tag `v0.22.1-port-sync-reconnect` posé** (local, non poussé, un par subsystème).

## État courant

**Terrain (déploiement `~/Projets/2026-06-29 keroro emule`) : amuled est en High-ID stable.**
La chaîne complète tourne : clé WireGuard Proton fraîche → port forwarded gluetun **stable** →
port-sync applique le port + restart amuled → `Connected with HighID` (clientid IP-based). Config
opérateur modifiée (dans le déploiement, **pas** dans le repo) : `config/crawler/crawler.yml`
(`port_sync.enabled: true`), `compose.yml` gluetun (`PORT_FORWARD_ONLY: "on"`), `.env`
(nouvelle clé WireGuard).

**Repo, gate complet vert** (crawler 731 / 100 % branche ; ruff, ruff format, mypy --strict OK ;
matching/verifier/webui intouchés) :

1. **`fix(port-sync)`** (`8842c6e`) — le `port_sync_loop` reconnecte désormais son client EC
   **à chaque cycle**. Ajout de `await deps.ports.connect()` en tête de `run_port_sync_cycle`
   (après le early-return `live is None`, avant tout appel EC) ; `connect()` ajouté au Protocol
   local `PortPreferences` (le vrai `AmuleEcClient` le satisfait déjà). Idempotent quand connecté,
   vraie reconnexion après un drop de transport self-healed ; un reconnect échoué (amuled down)
   relève sous `MuleClientError` → absorbé + backoff comme toute erreur EC.
2. **`docs(runbook)`** (`981ddf1`) — entrée troubleshooting « port forwarded qui change toutes les
   ~60 s (ProtonVPN + WireGuard) » : cause NAT-PMP, fix par clé fraîche/unique + Moderate NAT off,
   renvoi à [gluetun#3196](https://github.com/qdm12/gluetun/issues/3196).

## Ce qui a été construit / diagnostiqué

Deux causes **empilées** derrière le Low-ID, à ne pas confondre :

1. **`port_sync.enabled: false`** dans le `crawler.yml` du déploiement — le pont gluetun→amuled
   était éteint. → activé (config opérateur).
2. **Le port forwarded churnait toutes les ~60 s** (signature `requested X but received Y` à
   chaque renouvellement NAT-PMP) — problème **gluetun ⇄ Proton sur WireGuard**, pas le crawler.
   → clé WireGuard régénérée (PF activé, **Moderate NAT désactivé**, unique à l'instance).
3. **Bug de reconnexion EC** (le vrai défaut code, corrigé ici) — après que le port-sync a
   redémarré amuled, sa connexion EC dédiée mourait et n'était **jamais** rétablie
   (`EC client not connected (call connect() first)` en boucle), donc le port n'était jamais
   ré-appliqué.

## Pièges appris

- **Low-ID est l'état NORMAL par défaut** (le runbook le dit) ; le High-ID est optionnel. Ne pas
  traiter un Low-ID comme une panne tant que le port-sync n'est pas explicitement voulu.
- **`PORT_FORWARD_ONLY=on` ne corrige PAS le churn** (vérifié terrain : churn identique sur
  serveurs P2P). C'est nécessaire/sain mais insuffisant seul — le vrai levier est la **clé
  WireGuard** (Moderate NAT off + unique). Confirmé par plusieurs témoins de gluetun#3196.
- **Le churn est spécifique WireGuard** (paquets NAT-PMP UDP dans le tunnel). OpenVPN TCP le
  contourne aussi, mais Proton **déprécie OpenVPN** → garder WireGuard + clé propre est le bon choix.
- **`AmuleEcClient.connect()` est idempotent ET self-heal** : sur erreur de lecture, `_request`
  fait `close()` (annule le transport) puis relève → l'appel suivant voit `_transport is None`.
  D'où : appeler `connect()` en tête de cycle suffit à ressusciter la connexion (pas besoin d'un
  flag `_connected` local ; le client porte l'état). Pattern miroir de `SearchWorker._ensure_connected`.

## Prochaine étape suggérée

**Embarquer le fix dans l'image du nœud.** Le déploiement tourne sur `emule-indexer-crawler:latest`
(GHCR) — le fix est dans les **sources**, pas encore dans l'image. Sans urgence (le High-ID tient
tant que le port reste stable) ; le bug ne mordrait qu'à la **prochaine reconnexion VPN** (~3 h,
healthcheck gluetun → nouveau port). Pour l'embarquer : rebuild + publish de l'image crawler, puis
`docker compose pull crawler && up -d crawler` sur le déploiement.

## NON validé sur vrai matériel

- **Le fix EC n'a PAS été exercé end-to-end contre un amuled réel** : le nœud tourne encore
  l'ancienne image. La reconnexion après restart n'est prouvée que par le test unitaire (RED→GREEN)
  reproduisant le deadlock déconnecté. À confirmer après déploiement de la nouvelle image, sur une
  vraie reconnexion VPN (le port-sync doit ré-appliquer le nouveau port sans redémarrage du crawler).
- **Limitation mineure connue (hors scope, pré-existante) :** le re-check High-ID *dans le même
  cycle* que le restart tape sur la connexion morte et échoue (absorbé, silencieux) ; la
  convergence + le clear de l'alerte se font au **cycle suivant** via le `connect()` de tête. Donc
  l'événement `HighIdRecovered` peut ne pas être émis dans ce chemin (l'alerte est bien levée par
  `edge.leave` sur la branche « port aligné »). Émettre `HighIdRecovered` de façon fiable
  demanderait un `close()`+`connect()` autour du re-check — non fait (YAGNI, risque > bénéfice).
