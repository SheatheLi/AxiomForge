DELETE FROM target
WHERE str_column IN (SELECT str_column FROM stage)
  AND num_column IN (SELECT num_column FROM stage);
