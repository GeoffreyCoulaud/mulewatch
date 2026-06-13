-- local.db — migration 0002 : plafond disque applicatif (spec download §7 — DÉCISION D6).
-- Ajoute la taille du fichier à la table downloads (existante, migration 0001). Le plafond
-- reste une requête simple (somme des size_bytes des downloads ACTIFS). DEFAULT 0 exigé par
-- ALTER TABLE ADD COLUMN NOT NULL sur une table éventuellement non vide.

ALTER TABLE downloads ADD COLUMN size_bytes INTEGER NOT NULL DEFAULT 0;
