DELETE FROM target
WHERE EXISTS (SELECT 1 FROM stage AS s
 WHERE s.type IS target.type AND s.name IS target.name);
