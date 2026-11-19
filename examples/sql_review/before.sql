SELECT o.id AS fact_id, o.price AS amount
FROM orders AS o
LEFT JOIN (SELECT sku, COUNT(*) AS n FROM supplies GROUP BY sku) AS s
  ON o.sku = s.sku;
