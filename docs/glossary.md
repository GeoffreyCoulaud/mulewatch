# Glossaire

| Terme | Sens |
|---|---|
| **service** | Une brique de la pile : un conteneur géré par `docker compose`. Un nœud est un service, `mulewatch` (deux avec le VPN, qui ajoute `gluetun`). |
| **s6** | Le petit superviseur qui fait tourner les trois processus du conteneur (`amuled`, `amuleweb`, `mulewatch`) et en relance un s'il meurt. |
| **eD2k / Kad** | Les deux réseaux eMule surveillés : eDonkey2000 (serveurs centraux) et Kademlia (décentralisé, sans serveur). |
| **Low-ID / High-ID** | Le degré de joignabilité de votre nœud sur eD2k. High-ID = la machine est joignable depuis l'extérieur (plus de sources directes). Low-ID fonctionne aussi, simplement moins bien. |
| **IncomingDir** | Le dossier où le client eMule écrit un fichier terminé. Ici, il est monté en bind sur `downloads/incoming` dans votre dossier de travail. |

---
