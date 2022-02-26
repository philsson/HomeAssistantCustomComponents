# Daily Min Max
Track the min or max value of a sensor over the course of a day. The sensor will reset its value every 24h, by default at midnight.

## Example Configuration
```yaml
- platform: daily_min_max
  name: Pan Daily Max
  type: max
  entity_ids:
    - sensor.pan_temperature

- platform: daily_min_max
  name: Outdoor Daily Max
  type: max
  entity_ids:
    - sensor.outdoor_temp
    - sensor.indoor_temp
  time: "03:30:00"

- platform: daily_min_max
  name: Manually reset Only
  type: min
  entity_ids:
    - sensor.fuel_consumption
  manual_reset_only: True
```
