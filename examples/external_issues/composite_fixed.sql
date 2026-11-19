DELETE FROM target
WHERE (str_column,num_column) IN (SELECT str_column,num_column FROM stage);
