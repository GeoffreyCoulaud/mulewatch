# syntax=docker/dockerfile:1
# Image du serveur eD2k vendoré (spec e2e §5.1), buildé depuis vendor/ed2kd (commit f6c330da).
# Contexte de build = racine du dépôt (cf. compose.e2e.yaml : `context: .`).
#
# 3 ajustements cmake (toxicités relevées CMakeLists.txt:1,70-71) :
#   - CMAKE_POLICY_VERSION_MINIMUM=3.5 : le `cmake_minimum_required(VERSION 2.8.7)` est rejeté
#     par CMake >= 4 (Debian bookworm fournit CMake 3.25, donc surtout un filet de sécurité).
#   - CMAKE_BUILD_TYPE=Release + CMAKE_C_FLAGS_RELEASE="-O2 -DNDEBUG" : écrase les flags toxiques
#     `-Ofast -flto -march=native -funroll-loops` (l.70) — `-march=native` casse la portabilité
#     (build host != run host), `-flto`/`-Ofast` fragiles. sqlite3.c reste compilé par le
#     CMakeLists avec FTS4 (l.50,58), indispensable à la recherche `fnames MATCH`.

# --- build ---
FROM debian:bookworm AS build
RUN apt-get update && apt-get install -y --no-install-recommends \
        cmake build-essential libevent-dev libconfig-dev zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*
COPY vendor/ed2kd /src/ed2kd
WORKDIR /src/ed2kd/build
RUN cmake .. \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_C_FLAGS_RELEASE="-O2 -DNDEBUG" \
    && cmake --build . --target ed2kd -j

# --- runtime ---
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
        libevent-2.1-7 libconfig9 zlib1g \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /src/ed2kd/build/ed2kd /usr/local/bin/ed2kd
COPY deploy/e2e/ed2kd/ed2kd.conf /etc/ed2kd/ed2kd.conf
EXPOSE 4661
# ed2kd ne prend AUCUN flag de config (main.c:21 optString="vhg" ; main.c:109
# server_load_config(NULL) → config.c:11,39-40 CFG_DEFAULT_PATH="ed2kd.conf", chemin RELATIF).
# Donc : se placer dans le répertoire qui contient ed2kd.conf et lancer ed2kd nu.
WORKDIR /etc/ed2kd
ENTRYPOINT ["ed2kd"]
