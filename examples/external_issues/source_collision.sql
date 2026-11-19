DELETE FROM target
WHERE key IN (SELECT key FROM stage);
