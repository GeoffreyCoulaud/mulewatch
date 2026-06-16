#!/usr/bin/env bash
# Génère le média planté de la suite e2e (spec e2e §4.1) de façon reproductible.
#
# Le binaire produit (planted.mp4) est COMMITÉ dans le dépôt à côté de ce script ; le hash ed2k
# est calculé DEPUIS le binaire commité (constante PLANTED_ED2K_HASH dans tests/e2e/planted.py),
# JAMAIS depuis une re-génération — une version d'ffmpeg différente peut produire un octet
# différent (spec e2e §8, risque « déterminisme ffmpeg »). Ce script n'est utile que pour
# RE-générer le média (ffmpeg requis) ; un contributeur n'en a pas besoin (le binaire suffit).
#
# Le média est un petit clip valide (ffprobe passe → verdict verifier `clean` + real_meta non
# vide) dont le nom planté est « Keroro n°62 A.mp4 » (satisfait is_video + segment_id + keroro →
# cible S2E062A, spec e2e §4.3). Le NOM est métadonnée (porté par le lien ed2k / le partage) et
# n'influe PAS sur le hash (qui dépend du CONTENU) : le fichier sur disque s'appelle planted.mp4.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
out="${here}/planted.mp4"

ffmpeg -nostdin -hide_banner -loglevel error \
    -f lavfi -i "testsrc=duration=1:size=128x128:rate=10" \
    -f lavfi -i "sine=frequency=440:duration=1" \
    -c:v libx264 -c:a aac -shortest -y "${out}"

echo "écrit : ${out} ($(wc -c < "${out}") octets)"
echo "recalcule le hash : ( cd packages/crawler && python3 -c \"import sys; sys.path.insert(0,'tests'); from e2e.md4 import ed2k_hash; print(ed2k_hash(open('../../deploy/e2e/fixtures/planted.mp4','rb').read()))\" )"
