# Handoff — emule-indexer

> **But de ce document** : permettre à une nouvelle session (humaine ou IA) de **reprendre le développement naturellement**. Lis ceci en premier, puis le brief de cadrage et la spec (liens en bas).
>
> **Dernière mise à jour** : 2026-06-11, après l'achèvement du **moteur de matching** (Plans 1 → 2c, tag `v0.4.0-engine`).

---

## 1. TL;DR — où on en est, quoi faire ensuite

- **Objectif du projet** : surveiller en continu le réseau eMule (eD2k + Kad) pour retrouver les épisodes du **doublage VF de « Keroro mission Titar »** (lost media, diffusé 2008 sur Teletoon), et cataloguer un maximum de métadonnées. Le **sujet du catalogue est le fichier, pas la personne** — aucun pistage/désanonymisation.
- **Ce qui est fait** : tout le **moteur de matching (« le joyau »)** — domaine pur, du repli de chaîne jusqu'à la décision fichier déterministe et explicable. **170 tests, 100 % de couverture branche**, `ruff` + `mypy --strict` propres.
- **Ce qui reste** : tous les **consommateurs** du moteur (persistance, réseau/EC, download, observabilité, packaging). Le moteur produit une décision **en mémoire** ; rien ne la persiste ni n'agit dessus encore.
- **Prochaine étape recommandée** : voir §7. Mon conseil = **modèle de données** (`catalog.db`/`local.db`) **ou** **adapter EC** (le risque technique n°1 — dé-risquer tôt).

---

## 2. État actuel (vérifiable)

- Branche : `main`. **Commits non poussés** (pas de remote configuré). Travail directement sur `main` (greenfield, autorisé par le propriétaire).
- **Tags de jalons** (un par plan, annotés, **non poussés**) :
  - `v0.1.0-foundations` — toolchain, CI, hooks pré-push, `normalize`/`tokenize`, README.
  - `v0.2.0-matchers` — normalisation raffinée + modèles cible + interpolation + 4 matchers feuilles.
  - `v0.3.0-config-graph` — modèle de config, loader YAML, validation fail-fast, résolveur par cible.
  - `v0.4.0-engine` — **moteur d'évaluation complet**.
- **Gate vert** : `uv run pytest -q` → 170 passed, 100 % branch ; `ruff check` + `ruff format --check` + `mypy` propres (33 fichiers).
- **Méthode de travail employée** : skills *superpowers* (`brainstorming` → `writing-plans` → `subagent-driven-development` → revue finale → tag). Chaque plan a été **rédigé** (doc committé), **exécuté tâche-par-tâche** (un sous-agent implémenteur frais + revue spec + revue qualité par tâche), puis **revu holistiquement** par un dernier agent avant tag.

---

## 3. Carte de l'architecture (Clean / Hexagonal)

Layout `src/` (tout ce qui suit existe et est testé) :

```
src/emule_indexer/
├── domain/                       # PUR : aucune I/O, aucun import de lib réseau/DB/yaml
│   ├── normalization.py          # fold() (replie accents+casse, garde ponctuation/chiffres),
│   │                             #   normalize()/tokenize() (alnum→espaces, split)
│   └── matching/
│       ├── models.py             # FileCandidate (fichier observé), TargetSegment (.target_id="S2E062A")
│       ├── interpolation.py      # FRENCH_MONTHS, date_alternation_pattern(), interpolate(),
│       │                         #   InterpolationError ; placeholders {number}{segment}{title}{date_alt}
│       ├── matchers.py           # 4 matchers feuilles : KeywordMatcher, RegexMatcher (RE2),
│       │                         #   CoverageMatcher (rapidfuzz, .value()), AttrBetweenMatcher ;
│       │                         #   STOPWORDS_FR, ATTR_NAMES
│       ├── combinators.py        # Matcher (Protocol : .matches(candidate)->bool) ; All/Any/NotMatcher
│       ├── config.py             # modèle de config gelé (union étiquetée) : KeywordDef/RegexDef/
│       │                         #   CoverageDef/AttrBetweenDef/AllDef/AnyDef/NotDef/TokenRef/Rule/
│       │                         #   MatcherConfig ; type Operand, TokenDef ; TIERS
│       ├── validation.py         # parse_matcher_config() + parse_targets() (schéma + graphe) :
│       │                         #   ConfigError + sous-types UnknownTokenError/CycleError/
│       │                         #   DepthExceededError ; DAG/cycle nommé, profondeur 32,
│       │                         #   compile-check RE2 par sonde, unicité target_id
│       ├── resolver.py           # MatcherResolver(config).resolve_all(target) -> ResolvedTarget ;
│       │                         #   interpole+compile les regex PAR CIBLE, lie coverage au titre
│       └── engine.py             # MatchingEngine(config, targets).evaluate(candidate)
│                                 #   -> MatchDecision | None ; _TIER_RANK, Explanation
└── adapters/
    └── config/
        └── yaml_loader.py        # SEUL module qui importe `yaml` et touche le disque (load_yaml)
```

**Règle d'or** : `domain/` est pur. Toute I/O (YAML, plus tard EC/SQLite/apprise) vit dans `adapters/`. Le graphe de dépendances est un DAG : `config.py` est la feuille ; `engine.py` est au sommet (rien ne l'importe). `matchers.py` (Plan 2a) **satisfait** le Protocol `Matcher` **sans l'importer** (typage structurel).

---

## 4. Comment travailler ici (conventions — IMPÉRATIVES)

Le propriétaire (Geoffrey, francophone) tient à ces contraintes (voir aussi la mémoire auto du projet) :

- **Langages autorisés** : Python (ici **uniquement** Python, pas de repli Kotlin), Kotlin, Java, JS, TS. Il doit pouvoir **lire et comprendre tout le code** → pas de magie ; une dépendance « boîte noire » de confiance (aMule, Postgres, PyYAML) est OK.
- **Clean Architecture à la Bob Martin** : ports/adapters, domaine pur, pas de spaghetti, fichiers focalisés.
- **Toolchain** : `uv` (projet+paquets), `ruff` (`select=["E","F","I","UP","B","SIM"]`, **line-length 100**), `mypy --strict` (`files=["src","tests"]`), `pytest`+`pytest-cov`.
- **Gate de couverture 100 % branch, IMPOSÉ** (`--cov-fail-under=100`, `branch=true`). Le build échoue sous le seuil. **Ne jamais baisser le seuil** ; ajouter le test manquant. (re2 a un override mypy `ignore_missing_imports=true` ; google-re2 n'a pas de stubs.)
- **TDD STRICT** : les **tests sont la spec d'une feature**. **Aucun code de prod avant les tests.** La revue porte d'abord sur l'exactitude des tests. Annoter TOUTES les fns de test `-> None`, params typés.
- **Travail non pressé, creuser les pièges** : Geoffrey veut du travail soigné. Prendre le temps, anticiper les pièges (cf. §8). Pas d'urgence performative.
- **Déléguer aux sous-agents** : exécution des plans en **subagent-driven** (un implémenteur frais par tâche + revue spec puis revue qualité ; revue finale holistique avant le tag). Garde le contexte principal propre.
- **Git** : travailler sur `main` (greenfield). **Taguer chaque plan** (`vX.Y.Z-nom`, annoté) **sans pousser**. Messages de commit conventionnels (`feat(domain):`, `fix(domain):`, `test:`, `chore:`, `docs:`). Hooks pré-push installés (`.githooks/`, via `scripts/setup-dev.sh`) qui rejouent les 4 checks.
- **Commandes utiles** :
  ```bash
  uv sync --dev
  uv run pytest -q                      # tests + coverage (gate 100%)
  uv run ruff check . && uv run ruff format --check . && uv run mypy
  ```
- **context7 MCP** : pour toute question sur une lib/framework/SDK/CLI, utiliser context7 (docs à jour) plutôt que la mémoire d'entraînement.

---

## 5. Décisions verrouillées (NE PAS re-litiguer)

Issues de longues sessions de brainstorming ; détail complet dans la spec et le brief.

- **Moteur réseau = aMule seul** (`amuled` headless + API **EC** binaire). **MLDonkey inutile** (son Kad/Overnet est mort ; les serveurs eD2k sont partagés de toute façon). **G2/Gnutella/Shareaza hors scope.** **Pas de seeding.**
- **Adapter EC à écrire en Python** (aucune lib existante fiable). C'est **le gros inconnu technique** → à dé-risquer tôt (mesurer empiriquement la richesse des champs EC vs un `amuled` réel).
- **High ID via VPN** : homelab derrière **gluetun + ProtonVPN** ; **un seul port NAT-PMP** appliqué en **TCP=UDP même numéro** ; **glueforward** (projet de Geoffrey) à étendre d'un service `amule`. Killswitch gluetun = pas de fuite IP. Pas de VPS.
- **Deux bases SQLite** (modèle §11) : `catalog.db` (**append-only, adressé par contenu** = hash eD2k, mergeable multi-chercheurs, capture-all + `raw_meta` JSON) vs `local.db` (opérationnel, **jamais mergé** : file de tâches, downloads, scheduler). **Writer unique = le crawler.**
- **Moteur de matching** : minimal en code, **politique 100 % en config** (YAML). 4 types de tokens (`keyword`/`regex`/`coverage`/`attr_between`), tokens nommés + composites + règles ordonnées, validation fail-fast (DAG/profondeur/RE2). **RE2** (temps linéaire, anti-ReDoS — noms de fichiers = input hostile). **Décision déterministe** : palier max (`download>notify>catalog`), départage **index de règle puis `target_id`**.
- **Sécurité du contenu** : tout contenu réseau est **radioactif jusqu'à vérification**. Confinement non négociable (amuled sandboxé, **partage DÉSACTIVÉ** = on ne re-seede jamais un poison, quarantaine, no-exec). Verifier = service HTTP stateless sur réseau Docker `internal: true`, enfant jetable par fichier (`net=none`, rlimits, timeout-kill). **Vérification = NO-OP en MVP** (ClamAV en follow-up). gVisor/nsjail = durcissement opt-in **Linux** (pas Windows).
- **Deux modes de déploiement** (deux images Docker indépendantes) : **observer** (catalogue seul, portable, sans download/sandbox) vs **full** (download + verifier). Bascule par présence de la variable d'env du verifier.
- **Sorties** = logs structurés + **métriques Prometheus** + **notifications apprise** (avec lien `ed2k://`). UI web dépriorisée.

---

## 6. Le moteur de matching — API publique (ce qui est utilisable MAINTENANT)

Chaîne complète, de bout en bout (le test `tests/domain/matching/test_golden_corpus.py` le fait sur fixtures) :

```python
from emule_indexer.adapters.config.yaml_loader import load_yaml
from emule_indexer.domain.matching.validation import parse_matcher_config, parse_targets
from emule_indexer.domain.matching.engine import MatchingEngine
from emule_indexer.domain.matching.models import FileCandidate

config  = parse_matcher_config(load_yaml(config_path))     # schéma + DAG/profondeur/RE2, fail-fast (ConfigError)
targets = parse_targets(load_yaml(targets_path))           # tuple[TargetSegment, ...], target_id uniques garantis
engine  = MatchingEngine(config, targets)                  # pré-résout les arbres par cible UNE FOIS

decision = engine.evaluate(FileCandidate(filename="...", size_mb=None, duration_sec=None, bitrate_kbps=None))
# -> MatchDecision(target_id, rule_name, tier, explanation) | None  (None = fichier écarté)
#    explanation : Explanation(target_id, rules_fired, tokens_matched, coverage_values)
```

- **Déterministe** : la décision ne dépend pas de l'ordre des cibles (propriété P1, testée). Départage : palier max → index de règle → `target_id` (clé `min((-rang, index, target_id))`).
- **Explicable** : `Explanation` porte les règles/tokens déclenchés + la `value` de **chaque** token coverage (même sous le seuil — utile pour déboguer). Le moteur **retourne** l'explication, il ne **logge** pas (le logging sera un adapter).
- **`MatchDecision` = exactement les 3 colonnes de `match_decisions` (§11)** : `target_id`, `rule_name`, `tier`. Les colonnes de persistance (`id`, `ed2k_hash`, `decided_at`, `node_id`) seront injectées par l'**adapter DB**.
- **Format de config** : voir `tests/fixtures/canonical_config.yaml` (config canonique §8.3) + `tests/fixtures/canonical_targets.yaml` (§7) + `tests/fixtures/golden_corpus.yaml` (corpus extensible par la communauté : ajouter un cas = ajouter une entrée).

---

## 7. Quoi faire ensuite (plans restants)

Le moteur est complet ; il reste à **alimenter en données réelles** et **agir** sur ses décisions. Plans envisagés (chacun = un cycle spec→plan→exécution→tag, comme jusqu'ici) :

| Plan | Sujet | Pourquoi / dépendances |
|------|-------|------------------------|
| **A** | **Modèle de données** (`catalog.db` + `local.db`, schéma §11, append-only, file de tâches §12) | Base de tout le reste ; testable sur SQLite réel sans réseau. Faible risque. |
| **B** | **Adapter EC + observation** (connexion `amuled`, parsing des trames, recherche mot-clé §6) | **Le gros inconnu** — dé-risque tôt : mesurer empiriquement quels champs EC expose un `amuled` réel. Fixtures de trames + intégration opt-in. |
| **C** | **Orchestration des recherches** (cycles, scheduler, backoff par serveur, `effective_coverage`) | Dépend de B. Branche le moteur (§6) sur le flux d'observations. |
| **D** | **Auto-download + verifier + confinement** (§9/§10) | Mode full. Dépend du moteur (tier `download`) + EC + DB. |
| **E** | **Observabilité + notifications** (Prometheus, apprise, logs corrélés) | Transverse ; peut suivre A/B. |
| **F** | **Packaging + distribution** (2 images Docker, compose, glueforward service `amule`, outils CLI §15) | Dernier ; onboarding chercheurs Discord. |

**Recommandation de séquencement** : **A puis B** (ou **B d'abord** si on veut dé-risquer l'inconnu EC au plus tôt — c'est défendable). A et B sont largement indépendants. **C** a besoin de B ; **D** a besoin de A+B+moteur. Demander à Geoffrey lequel attaquer ; **brainstormer** d'abord (skill `brainstorming`) car ces sous-systèmes ont des choix de design ouverts (schéma exact des tables, stratégie de parsing EC, modèle de la file de tâches).

**Comment démarrer un plan** : `brainstorming` (si design ouvert) → `writing-plans` (déléguer la rédaction à un sous-agent Opus, comme les plans 2b/2c, avec : interfaces existantes exactes, conventions, extraits de spec, décisions à verrouiller) → relecture structurelle → validation de Geoffrey → `subagent-driven-development` → revue finale → tag `v0.5.0-...`.

---

## 8. Pièges & faits empiriques appris (gagne du temps)

**Pièges attrapés par les revues** (souvent par la **revue finale holistique** — la garder !) :
- **Validation non ordre-sensible** : une vérif sémantique (override coverage-only) faite pendant le parsing incrémental rejetait une référence *en avant*. Leçon : le **parsing est purement structurel** ; les vérifs qui ont besoin de la table complète vont dans la **passe de graphe** (`validate_config`).
- **Récursion vs borne** : un validateur récursif (cycle/profondeur) lève `RecursionError` sur une chaîne pathologique avant la borne métier → ajouter un garde-fou `len(stack) >= max_depth`. Penser aux **inputs adverses** (config contribuée par PR, noms de fichiers hostiles).
- **Déterminisme** : `target_id` met la lettre de segment en majuscule → `'a'` et `'A'` collisionnent ; sans unicité garantie, la décision devenait ordre-dépendante. Fermé dans `parse_targets`. Leçon : un **min-key déterministe exige des clés uniques** — le garantir fail-fast.
- **Tests tautologiques / non isolants** : un test `assert isinstance(x, type(x))` (toujours vrai) ; un test « palier max inter-cibles » qui ne distinguait pas l'effet intra-cible. Les revues qualité les ont attrapés. **Un test doit pouvoir échouer pour la bonne raison.**

**Faits empiriques vérifiés** (pas besoin de re-tester) :
- `google-re2` s'importe sous le nom **`re2`** ; pattern invalide → **`re2.error`** (alias public de `re2._re2.Error`) ; pas de stubs (override mypy en place). RE2 **ne supporte ni lookaround ni backreferences** → pour un garde de bord chiffre, utiliser un garde **consommant** `(?:^|[^0-9])` / `(?:[^0-9]|$)`, pas `\b` (qui bloque aussi `_`/lettres) ni `(?<!\d)`.
- `string.Formatter` **casse** sur les quantificateurs RE2 `{2,4}` (les prend pour des champs nommés) → l'interpolation utilise un scanner `re` `\{([a-zA-Z_]\w*)\}`.
- Normalisation : **NFKD ne décompose pas** `œ`/`æ` → table explicite `{œ→oe, æ→ae}` ; `casefold()` gère `ß→ss` (plus correct que `lower()`).
- PyYAML `safe_load` parse les dates ISO en `datetime.date` ; `mypy --strict` exige `types-pyyaml` (dev) pour `import yaml`.
- Coverage : un stub de Protocol **`def m(...) -> bool: ...` sur UNE ligne** est couvert (le `def` s'exécute) ; un `case _: assert_never(x)` exige `# pragma: no cover` (ligne logiquement morte mais comptée). `exclude_also` **étend** les excludes par défaut (qui incluent `pragma: no cover`).
- mypy `--strict` : `re2.compile(...)` renvoie `Any` → `... is not None` redonne un `bool` (pas de `warn-return-any`) ; `re2.escape(...)` renvoie `Any` → l'envelopper `str(...)`.

**Carry-overs pour les plans consommateurs** :
- L'**adapter DB** (§11) enveloppe `MatchDecision` (3 colonnes) avec `id`/`ed2k_hash`/`decided_at`/`node_id`. `Explanation` est gelée/hashable/JSON-friendly (tuples de str/float) → sérialisable pour une colonne d'audit DEBUG.
- La **politique auto-download** (§9 « tout sauf complet ») se branche sur `decision.tier == "download"` + `TargetSegment.status ∈ {lost, partial, poor}` (jointure `target_id → status`).
- `MatchingEngine.evaluate` est O(cibles × règles × taille-arbre) sans entonnoir (par choix, §8.5). OK pour des dizaines de segments Keroro ; à revisiter si le catalogue explose. `_explain` ne tourne que pour la cible gagnante, jamais sur un fichier écarté.
- La **cible-sonde** du compile-check regex (`validation._PROBE_TARGET`) code en dur `number/segment/title/date_alt` : tout nouveau placeholder d'interpolation devra l'étendre en parallèle.

---

## 9. Où tout se trouve

- **Spec MVP (autorité)** : `docs/superpowers/specs/2026-06-10-crawler-mvp-design.md` (17 sections ; §7 cibles, §8 moteur, §9 download, §10 sécurité, §11 données, §16 tests).
- **Handoffs** (dont le brief de cadrage de tout début) : `docs/handoffs/` — fichiers `<date ISO> - handoff - <contexte>.md` (le cadrage initial : `2026-06-10 - handoff - knowledge brief.md` ; ce document : `2026-06-11 - handoff - moteur de matching complet.md`).
- **Plans (exécutés)** : `docs/superpowers/plans/2026-06-10-crawler-mvp-{01-foundation,02a-matchers,02b-config-graph,02c-engine}.md`. Chaque plan a un en-tête + des tâches TDD complètes + une self-review.
- **Fixtures de référence** (forme de config réelle) : `tests/fixtures/*.yaml`.
- **Mémoire auto** (chargée chaque session) : `~/.claude/projects/-home-geoffrey-Repositories-emule-indexer/memory/` — style de code, décisions projet, feedback de méthode (TDD-first, non pressé, sous-agents), réf glueforward.

---

*Bonne continuation. Le cœur (le « joyau ») est posé, testé et déterministe — la suite, c'est le brancher au monde réel (EC) et lui donner une mémoire (DB), sans jamais relâcher le confinement ni le gate de couverture.*
