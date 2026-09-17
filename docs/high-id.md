# Devenir High-ID

Par défaut, votre nœud est en **Low-ID** : il catalogue et télécharge, mais avec moins de sources
directes. Passer en **High-ID** (joignable depuis l'extérieur) apporte plus de sources et une
recherche plus efficace. Ce n'est **pas obligatoire** pour cataloguer. Deux routes, selon votre
pile :

| Route | Comment l'activer |
|---|---|
| **Pile par défaut, port ouvert** | Redirigez le port `4662` (en TCP **et** en UDP) depuis votre routeur vers cette machine. Si vous changez de port, changez-le dans la section `ports:` de `compose.yml`. |
| **Pile VPN (gluetun), port forwarding** | Mettez `VPN_PORT_FORWARDING=on` dans votre `.env` **et** `port_sync.enabled: true` dans `crawler.yml`. Votre fournisseur VPN doit gérer le port forwarding ([liste gluetun](https://github.com/qdm12/gluetun-wiki/tree/main/setup/providers)). |

Sur la route VPN, le nœud aligne désormais le client eMule sur le port forwardé entièrement **dans
son propre conteneur** : il redémarre ce seul processus avec `s6-svc`. Il n'y a plus ni socket
Docker, ni proxy de socket, ni service supplémentaire dans la boucle.

Compromis, activation pas à pas et vérification :
[runbook d'administration, § High-ID](high-id.md).

---

Par défaut, un nœud est en **Low-ID** : il fonctionne très bien ainsi (recherche, catalogage,
téléchargement), il est juste sous-optimal côté sources. Le **High-ID** rend la machine **joignable**
depuis l'extérieur (plus de sources directes) ; c'est **facultatif**. Pour être joignable, il faut
qu'un **port entrant** atteigne amuled : deux routes, selon que vous gardez ou non le VPN devant le
trafic P2P.

### Route A (recommandée) : derrière le VPN, via port forwarding

**Comment ça marche.** gluetun sait demander un **port forwarding** à votre fournisseur VPN : le
port joignable est celui du VPN, **tout le trafic reste derrière le tunnel**. Le crawler interroge
le serveur de contrôle de gluetun, et quand le port a changé, il redémarre amuled pour qu'il
écoute sur le nouveau.

Depuis la 2.0, **Docker n'intervient plus du tout** dans cette boucle : le socket Docker, le
service `docker-proxy` et les réseaux dédiés ont disparu. Le crawler et amuled sont deux processus
du même conteneur, donc le redémarrage est un `s6-svc -r /etc/services.d/amuled` local. Et sous la
stack VPN, mulewatch partage la pile réseau de gluetun (`network_mode: service:gluetun`), donc le
serveur de contrôle est sur `localhost`.

C'est aussi plus juste qu'avant : le port n'a jamais été re-bindable à chaud, donc le port-sync a
toujours eu besoin d'un redémarrage de **processus** ; il redémarrait un **conteneur** seulement
parce que le processus était hors de portée.

**Configuration, deux réglages solidaires :**

1. **VPN avec port forwarding** + `VPN_PORT_FORWARDING=on` dans `.env` (cherchez les fournisseurs
   marqués `PORT_FORWARDING: yes` dans la [liste gluetun](https://github.com/qdm12/gluetun-wiki/tree/main/setup/providers)).
2. Dans `crawler.yml` : basculez `port_sync.enabled: true` (le bloc est présent par défaut,
   `gluetun_control_url` pointant déjà sur `http://localhost:8000` ; réglage fin optionnel via les
   autres champs de la section).

Cela n'a de sens que sous `gluetun.compose.yml` : dans la stack par défaut, il n'y a pas de serveur
de contrôle gluetun sur `localhost:8000`, et le port-sync tournera dans le vide — la dégradation
étant tolérée (Low-ID, backoff, alerte de repli sur le canal *operations*), il n'arrêtera pas le
nœud pour autant.

Une fois actif, surveillez les events `port-sync` / `High-ID retrouvé` dans les logs et les
métriques `emule_port_*`.

### Route B : ouvrir un port vous-même

Si votre fournisseur ne fait pas de port forwarding, le High-ID reste atteignable en
**ouvrant/redirigeant un port** sur votre box/routeur vers le nœud, pour que les pairs joignent amuled
directement. C'est une option **parfaitement viable** ; le choix relève surtout de votre **tolérance
au risque**.

> #### À savoir
> - **Légalité.** Partager une œuvre sous droit d'auteur est illégal **dans la plupart des
>   juridictions** : c'est vrai dès qu'on fait tourner un nœud, route B ou non. Le risque pratique
>   pour ce projet est **statistiquement faible** (eMule est un réseau de niche en 2026, et la cible,
>   des médias perdus aux ayants droit inactifs, mobilise peu) mais **n'est pas nul** ; il dépend
>   surtout de votre juridiction. Voir [`docs/legal-and-privacy.md`](legal.md) pour la
>   discussion détaillée (ce que le catalogue stocke et ne stocke pas, ce qu'un VPN protège vraiment,
>   responsabilités de l'opérateur).
> - **Surface d'attaque réseau.** Un port entrant ouvert, c'est un point d'entrée de plus sur votre
>   réseau domestique : redirigez **précisément** ce port (pas une plage) et gardez la machine à jour.
>
> La **route A** garde tout derrière le VPN sans ouvrir de port chez vous ; le **Low-ID**, lui,
> convient déjà très bien si vous voulez juste contribuer au catalogage sans optimiser les sources.

---

