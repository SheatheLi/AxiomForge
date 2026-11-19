SELECT o.id AS fact_id, o.price AS amount
FROM orders AS o
LEFT JOIN (SELECT sku, SUM(cost) AS total_supply_cost FROM supplies GROUP BY sku) AS s
  ON o.sku = s.sku;
