# Scout production health

Generated 2026-11-07T14:23:36+00:00

**VERIFIED_NATURAL_EXECUTION**

Next configured opportunity: 2026-11-07T14:17:00-05:00 (America/New_York); 2026-11-07T19:17:00+00:00 UTC.

Healthy certified employers: 138; scheduled boards: 336; source states: {'HEALTHY_COMPLETE': 230, 'STALE': 68, 'BLOCKED': 3, 'FAILED': 35}.
Due boards: 88; requested/effective cadence misses: 102 / 95.

New source-membership events: 9267; relevant internships: 46; verified public deliveries: 46; timestamp-qualified latency observations: 5.

Seven-day window: 2026-11-05T00:00:00-05:00; completed calendar days: 2; evaluation complete: False.

Natural first-attempt workflow records are required. Manual dispatches never satisfy this requirement. Missing API evidence is not proof that no workflow ran. Scanner COMPLETED alone does not prove checkpoint or Pages success.

## Daily evidence

| Date | Expected natural | Observed | Successful | Failed | Missing |
|---|---:|---:|---:|---:|---:|
|2026-11-05|4|2|2|0|2|
|2026-11-06|4|2|2|0|2|

## Provider observations

```json
{
  "ashby": {
    "attempts": 3,
    "complete": 3,
    "statuses": {
      "SUCCESS": 2,
      "SUCCESS_NO_MATCHING_JOBS": 1
    },
    "failure_categories": {
      "COMPLETE_SUCCESS": 3
    },
    "distinct_sources": 2,
    "mean_seconds": 0.3333058456666408,
    "p95_seconds": 0.5035279700000008,
    "retries": 0,
    "retry_seconds": 0
  },
  "eightfold": {
    "attempts": 18,
    "complete": 14,
    "statuses": {
      "PARSER_ERROR": 4,
      "SUCCESS": 12,
      "SUCCESS_NO_MATCHING_JOBS": 2
    },
    "failure_categories": {
      "PARSER_ERROR": 3,
      "COMPLETE_SUCCESS": 14,
      "INVENTORY_COMPLETENESS": 1
    },
    "distinct_sources": 9,
    "mean_seconds": 18.58111051,
    "p95_seconds": 69.90145162100005,
    "retries": 4,
    "retry_seconds": 21.49464967699999
  },
  "greenhouse": {
    "attempts": 46,
    "complete": 45,
    "statuses": {
      "SUCCESS": 38,
      "SUCCESS_NO_MATCHING_JOBS": 7,
      "ATS_CHANGED": 1
    },
    "failure_categories": {
      "COMPLETE_SUCCESS": 45,
      "PUBLICATION_CHANGED_OR_UNAVAILABLE": 1
    },
    "distinct_sources": 27,
    "mean_seconds": 0.8736681089782606,
    "p95_seconds": 2.54513721799999,
    "retries": 0,
    "retry_seconds": 0
  },
  "icims": {
    "attempts": 62,
    "complete": 62,
    "statuses": {
      "SUCCESS": 25,
      "SUCCESS_NO_MATCHING_JOBS": 37
    },
    "failure_categories": {
      "COMPLETE_SUCCESS": 62
    },
    "distinct_sources": 49,
    "mean_seconds": 2.638466889806452,
    "p95_seconds": 9.272385449999998,
    "retries": 0,
    "retry_seconds": 0
  },
  "lever": {
    "attempts": 11,
    "complete": 11,
    "statuses": {
      "SUCCESS": 7,
      "SUCCESS_NO_MATCHING_JOBS": 4
    },
    "failure_categories": {
      "COMPLETE_SUCCESS": 11
    },
    "distinct_sources": 7,
    "mean_seconds": 5.103616035181804,
    "p95_seconds": 33.946846252,
    "retries": 0,
    "retry_seconds": 0
  },
  "oracle": {
    "attempts": 21,
    "complete": 21,
    "statuses": {
      "SUCCESS": 19,
      "SUCCESS_NO_MATCHING_JOBS": 2
    },
    "failure_categories": {
      "COMPLETE_SUCCESS": 21
    },
    "distinct_sources": 11,
    "mean_seconds": 12.734201438047618,
    "p95_seconds": 28.344130013000008,
    "retries": 0,
    "retry_seconds": 0
  },
  "phenom": {
    "attempts": 44,
    "complete": 34,
    "statuses": {
      "PARSER_ERROR": 9,
      "SUCCESS": 30,
      "SUCCESS_NO_MATCHING_JOBS": 4,
      "TIMEOUT": 1
    },
    "failure_categories": {
      "INVENTORY_COMPLETENESS": 9,
      "COMPLETE_SUCCESS": 34,
      "TIMEOUT": 1
    },
    "distinct_sources": 19,
    "mean_seconds": 23.000010711386363,
    "p95_seconds": 80.64794587799993,
    "retries": 10,
    "retry_seconds": 673.781004034
  },
  "smartrecruiters": {
    "attempts": 30,
    "complete": 30,
    "statuses": {
      "SUCCESS_NO_MATCHING_JOBS": 6,
      "SUCCESS": 24
    },
    "failure_categories": {
      "COMPLETE_SUCCESS": 30
    },
    "distinct_sources": 15,
    "mean_seconds": 11.776705002033331,
    "p95_seconds": 67.80871699100003,
    "retries": 0,
    "retry_seconds": 0
  },
  "talentbrew": {
    "attempts": 92,
    "complete": 71,
    "statuses": {
      "PARSER_ERROR": 20,
      "SUCCESS": 48,
      "SUCCESS_NO_MATCHING_JOBS": 23,
      "UNKNOWN_ERROR": 1
    },
    "failure_categories": {
      "INVENTORY_COMPLETENESS": 13,
      "COMPLETE_SUCCESS": 71,
      "UNKNOWN_ERROR": 1,
      "PARSER_ERROR": 1,
      "DETAIL_HYDRATION": 6
    },
    "distinct_sources": 42,
    "mean_seconds": 4.135811948347827,
    "p95_seconds": 14.976889579999987,
    "retries": 17,
    "retry_seconds": 39.820215545000075
  },
  "workday": {
    "attempts": 322,
    "complete": 254,
    "statuses": {
      "SUCCESS": 208,
      "BLOCKED": 8,
      "PARSER_ERROR": 52,
      "SUCCESS_NO_MATCHING_JOBS": 46,
      "ATS_CHANGED": 5,
      "HTTP_ERROR": 1,
      "TIMEOUT": 2
    },
    "failure_categories": {
      "COMPLETE_SUCCESS": 254,
      "ACCESS_RESTRICTION": 8,
      "REQUISITION_IDENTITY": 3,
      "PUBLICATION_CHANGED_OR_UNAVAILABLE": 5,
      "HTTP_ERROR": 1,
      "PARSER_ERROR": 49,
      "TIMEOUT": 2
    },
    "distinct_sources": 155,
    "mean_seconds": 21.102186365919252,
    "p95_seconds": 82.96893263899995,
    "retries": 47,
    "retry_seconds": 1217.0215086710002
  }
}
```

## Backlog causes

```json
{
  "AWAITING_SCHEDULER_OPPORTUNITY": 58,
  "SOURCE_FAILURE_RETRY": 30
}
```

Data are evaluated at this report clock. Historical successful delivery does not promise a job remains open. The checkpoint report precedes its own final GitHub publication; the following run collects its completed platform record.
