SELECT event_id,event_at,amount FROM source WHERE event_at >= :start AND event_at < :end;
