-- Partial indexes for onward routing, date windows, and inbound calendars.
-- Empty bases have no row entries. Avoid indexing every possible filter.
CREATE INDEX IF NOT EXISTS flight_legs_scheduled_origin
ON flight_legs(origin_iata, departure_scheduled_us)
WHERE status <> 'cancelled' AND departure_scheduled_us IS NOT NULL
    AND arrival_scheduled_us IS NOT NULL;
CREATE INDEX IF NOT EXISTS flight_legs_scheduled_departure
ON flight_legs(departure_scheduled_us)
WHERE status <> 'cancelled' AND departure_scheduled_us IS NOT NULL
    AND arrival_scheduled_us IS NOT NULL;
CREATE INDEX IF NOT EXISTS flight_legs_actual_origin
ON flight_legs(origin_iata, departure_actual_us)
WHERE status <> 'cancelled' AND departure_actual_us IS NOT NULL
    AND arrival_actual_us IS NOT NULL;
CREATE INDEX IF NOT EXISTS flight_legs_actual_departure
ON flight_legs(departure_actual_us)
WHERE status <> 'cancelled' AND departure_actual_us IS NOT NULL
    AND arrival_actual_us IS NOT NULL;
CREATE INDEX IF NOT EXISTS flight_legs_observed_origin
ON flight_legs(origin_iata, departure_observed_lower_us)
WHERE status <> 'cancelled' AND departure_observed_lower_us IS NOT NULL;
CREATE INDEX IF NOT EXISTS flight_legs_observed_departure
ON flight_legs(departure_observed_lower_us)
WHERE status <> 'cancelled' AND departure_observed_lower_us IS NOT NULL;

-- Calendar arrival windows use the destination and earliest arrival bound.
-- Keep incomplete/cancelled facts out of these indexes, as with onward routing.
CREATE INDEX IF NOT EXISTS flight_legs_scheduled_destination
ON flight_legs(destination_iata, arrival_scheduled_us)
WHERE status <> 'cancelled' AND departure_scheduled_us IS NOT NULL
    AND arrival_scheduled_us IS NOT NULL;
CREATE INDEX IF NOT EXISTS flight_legs_actual_destination
ON flight_legs(destination_iata, arrival_actual_us)
WHERE status <> 'cancelled' AND departure_actual_us IS NOT NULL
    AND arrival_actual_us IS NOT NULL;
CREATE INDEX IF NOT EXISTS flight_legs_observed_destination
ON flight_legs(destination_iata, arrival_observed_lower_us)
WHERE status <> 'cancelled' AND departure_observed_lower_us IS NOT NULL;

-- Relaxed airport adjacency for routing lower bounds. Covering these two short
-- codes avoids reading flight metadata while checking geographic reachability.
-- Admission and exact time windows remain the job of searchable_legs queries.
CREATE INDEX IF NOT EXISTS flight_legs_airport_pair
ON flight_legs(origin_iata, destination_iata);
