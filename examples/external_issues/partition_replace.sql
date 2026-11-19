DELETE FROM target
WHERE date_day IN (SELECT date_day FROM stage);
