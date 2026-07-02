# Handoff — gate média sur le tier `catalog` (vidéo ou archive)

> Branche `fix/catalog-media-only` (in-place), **mergée en local dans `main`** (fast-forward
> `b0bd077..aaf9af4`). **Pas de tag** (ajustement de policy d'une ligne). Spec **inline** (changement
> simple, pas de doc `specs/`/`plans/`). TDD strict, exécution **inline** (3 fichiers, localisé).
> Suite directe du handoff `2026-07-02 - handoff - webui files matched-only default.md`.

## Point de départ

Geoffrey voyait dans `/files` (désormais matché-only par défaut) beaucoup de fichiers non
pertinents — mangas et audio : `… Sergent Keroro … .cbz`, `Keroro … .cbr`, `Keroro … .pdf`,
`Keroro.mp3`. Demande : **tout ce qui n'est ni vidéo ni archive doit être exclu d'office**.

**Ce n'était pas un bug de matching, mais une décision de design assumée.** La règle fourre-tout
`keroro_large` (tier `catalog`) était `all: [is_keroro]` — **aucune contrainte de type de média** :
n'importe quel fichier Keroro non-étranger décrochait une décision `catalog`, quelle que soit
l'extension. Le golden corpus l'encodait explicitement (cas `.txt` et `.mp3` → `catalog`). Le tier
`catalog` était le « catalogue permissif » (invariant « cataloguer toute la métadonnée », cf. spec
`2026-07-01-search-simplification-antimatch.md`). **Ce handoff resserre volontairement ce tier** —
la note « catalogue permissif sur `is_keroro` » de cette spec est donc **partiellement inversée**.

## Ce qui a été fait (option A)

Une ligne de policy + tests. `deploy/config/crawler/matcher.yml` :

```yaml
- { name: keroro_large, tier: catalog, all: [is_keroro, { any: [is_video, is_archive] }] }
```

- **Option A choisie** (vs B) : on agit sur la **décision de match**, pas sur l'**observation**. Les
  mp3/pdf/cbz restent enregistrés en observations brutes (`file_observations`, `record_observation`
  s'exécute toujours) ; ils cessent juste de décrocher une décision → **disparaissent de `/files`
  par défaut** (matché-only) et **ne polluent plus la couverture des cibles**. Consultables via
  « Show all catalogued files ». Ça s'imbrique avec le toggle matché/all du handoff précédent, sans
  toucher au crawler ni au schéma (invariant « la policy vit à un seul endroit » respecté).
- **cbz/cbr sont des formats COMICS** (ZIP/RAR déguisés) mais leurs extensions ne sont **pas** dans
  `is_archive` (`zip|7z|rar|r\d\d|z\d\d|part\d+\.rar`) → écartés sans traitement spécial. Verrouillé
  à deux niveaux : nouveau **test de contrat du token `is_archive`** (`test_golden_corpus.py` : matche
  zip/7z/rar/r01/z01/part1.rar, **rejette** cbz/cbr) + cas golden bout-en-bout (les 4 noms réels de
  Geoffrey → `discarded`).
- **Cas positif** `Keroro rediffusion.zip` → `catalog`/`keroro_large` : une archive générique **reste**
  cataloguée (peut contenir l'épisode) — verrouille la branche `is_archive` du gate.

Golden corpus : 2 cas basculés (`.txt`, `.mp3` : `catalog` → `discarded`, ids renommés) + 4 ajoutés.

## Pièges appris

1. **Seuls le golden corpus et les tests lisant le vrai `matcher.yml` étaient concernés.** Tous les
   autres tests moteur/crawler utilisent des configs *inline* (leur propre `keroro_large` sans gate)
   → découplés. Vérifié exhaustivement : tous les cas `catalog` existants (golden + `test_engine`
   `_canonical_*` + crawler `test_record_observations`/`test_app`) portent des noms `.avi`/`.mkv`
   (`"keroro something.avi"`, `"Keroro rediffusion.mkv"`) → **rien ne casse**, seuls les 2 cas non-média
   basculent.
2. **`golden_corpus.yaml` est en français EXPRÈS** — le commit de traduction `9eb4714` a traduit le
   harnais `test_golden_corpus.py` (Python) mais **délibérément laissé le fixture YAML en français**
   (corpus de noms eMule + annotations = donnée de domaine, carve-out « non-ASCII test fixtures »).
   Donc : commentaires YAML en **français**, commentaires Python en **anglais**. Ne pas « corriger ».
3. **Le gate n'ajoute aucune branche de code** (`is_video`/`is_archive` sont déjà des tokens utilisés
   ailleurs) → couverture 100 % branche préservée sans test moteur supplémentaire.

## Prochaine étape possible / non validé

- **Rien à valider sur hardware** : changement 100 % policy/tests, gate complet vert (matching 220,
  crawler 731, verifier 176, webui 105, ruff/mypy/sqlfluff/check_templates).
- **Décision ouverte si Geoffrey change d'avis** : option B (ne **plus enregistrer** l'observation des
  non-média) reste possible mais plus lourde (filtre média dans le chemin d'observation du crawler →
  duplique la policy hors `matcher.yml`, OU refactor pour router les observations par le moteur) et
  rendrait le toggle « Show all » quasi inutile pour ces fichiers. Écartée pour l'instant.
- **`is_video` reste `avi|mkv|mp4|mpg|mpeg|divx|m4v|ogm`** — si des épisodes VF circulent en `.wmv`/
  `.rmvb`/`.ts`, ils seraient aujourd'hui écartés du catalogue comme non-média. À surveiller sur les
  vraies observations réseau ; élargir la liste si besoin (même token partagé par download/notify).
