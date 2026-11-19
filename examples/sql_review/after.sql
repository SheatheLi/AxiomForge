SELECT o.id AS fact_id, o.price AS amount
FROM orders AS o
LEFT JOIN supplies AS s ON o.sku = s.sku;
