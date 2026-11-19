DELETE FROM target
WHERE EXISTS (SELECT 1 FROM stage AS s
 WHERE s.type = target.type AND s.name = target.name);
