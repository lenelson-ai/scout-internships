# Scout production health

Generated 2026-11-14T07:55:07+00:00

**VERIFIED_NATURAL_EXECUTION**

Next configured opportunity: 2026-11-14T08:17:00-05:00 (America/New_York); 2026-11-14T13:17:00+00:00 UTC.

Healthy certified employers: 124; scheduled boards: 336; source states: {'STALE': 70, 'HEALTHY_COMPLETE': 191, 'BLOCKED': 8, 'FAILED': 67}.
Due boards: 102; requested/effective cadence misses: 113 / 111.

New source-membership events: 27711; relevant internships: 150; verified public deliveries: 150; timestamp-qualified latency observations: 9.

Seven-day window: 2026-11-05T00:00:00-05:00; completed calendar days: 7; evaluation complete: False.

Natural first-attempt workflow records are required. Manual dispatches never satisfy this requirement. Missing API evidence is not proof that no workflow ran. Scanner COMPLETED alone does not prove checkpoint or Pages success.

## Daily evidence

| Date | Expected natural | Observed | Successful | Failed | Missing |
|---|---:|---:|---:|---:|---:|
|2026-11-05|4|2|2|0|2|
|2026-11-06|4|2|2|0|2|
|2026-11-07|4|3|3|0|1|
|2026-11-08|4|3|3|0|1|
|2026-11-09|4|2|2|0|2|
|2026-11-10|4|2|2|0|2|
|2026-11-11|4|2|1|1|2|

## Provider observations

```json
{
  "ashby": {
    "attempts": 11,
    "complete": 11,
    "statuses": {
      "SUCCESS": 7,
      "SUCCESS_NO_MATCHING_JOBS": 4
    },
    "failure_categories": {
      "COMPLETE_SUCCESS": 11
    },
    "distinct_sources": 2,
    "mean_seconds": 0.32691809963635604,
    "p95_seconds": 0.6970381700000019,
    "retries": 0,
    "retry_seconds": 0
  },
  "eightfold": {
    "attempts": 61,
    "complete": 47,
    "statuses": {
      "PARSER_ERROR": 14,
      "SUCCESS": 39,
      "SUCCESS_NO_MATCHING_JOBS": 8
    },
    "failure_categories": {
      "PARSER_ERROR": 9,
      "COMPLETE_SUCCESS": 47,
      "INVENTORY_COMPLETENESS": 5
    },
    "distinct_sources": 9,
    "mean_seconds": 18.3432641902459,
    "p95_seconds": 63.662714986999994,
    "retries": 14,
    "retry_seconds": 244.586689109
  },
  "greenhouse": {
    "attempts": 159,
    "complete": 146,
    "statuses": {
      "SUCCESS": 122,
      "SUCCESS_NO_MATCHING_JOBS": 24,
      "ATS_CHANGED": 10,
      "PARSER_ERROR": 3
    },
    "failure_categories": {
      "COMPLETE_SUCCESS": 146,
      "PUBLICATION_CHANGED_OR_UNAVAILABLE": 10,
      "PARSER_ERROR": 3
    },
    "distinct_sources": 27,
    "mean_seconds": 0.8214593490251579,
    "p95_seconds": 2.54513721799999,
    "retries": 12,
    "retry_seconds": 1.47745379799997
  },
  "icims": {
    "attempts": 228,
    "complete": 224,
    "statuses": {
      "SUCCESS": 83,
      "SUCCESS_NO_MATCHING_JOBS": 141,
      "PARSER_ERROR": 4
    },
    "failure_categories": {
      "COMPLETE_SUCCESS": 224,
      "PARSER_ERROR": 4
    },
    "distinct_sources": 49,
    "mean_seconds": 2.8288794257061407,
    "p95_seconds": 10.411147559999995,
    "retries": 3,
    "retry_seconds": 3.857086176999972
  },
  "lever": {
    "attempts": 38,
    "complete": 38,
    "statuses": {
      "SUCCESS": 25,
      "SUCCESS_NO_MATCHING_JOBS": 13
    },
    "failure_categories": {
      "COMPLETE_SUCCESS": 38
    },
    "distinct_sources": 7,
    "mean_seconds": 3.0542688112894694,
    "p95_seconds": 16.956518320999987,
    "retries": 0,
    "retry_seconds": 0
  },
  "oracle": {
    "attempts": 70,
    "complete": 69,
    "statuses": {
      "SUCCESS": 61,
      "SUCCESS_NO_MATCHING_JOBS": 8,
      "PARSER_ERROR": 1
    },
    "failure_categories": {
      "COMPLETE_SUCCESS": 69,
      "INVENTORY_COMPLETENESS": 1
    },
    "distinct_sources": 11,
    "mean_seconds": 13.036729842457145,
    "p95_seconds": 30.831527254999994,
    "retries": 1,
    "retry_seconds": 31.27330887700002
  },
  "phenom": {
    "attempts": 137,
    "complete": 117,
    "statuses": {
      "PARSER_ERROR": 18,
      "SUCCESS": 102,
      "SUCCESS_NO_MATCHING_JOBS": 15,
      "TIMEOUT": 1,
      "NETWORK_ERROR": 1
    },
    "failure_categories": {
      "INVENTORY_COMPLETENESS": 16,
      "COMPLETE_SUCCESS": 117,
      "TIMEOUT": 1,
      "DNS": 1,
      "PARSER_ERROR": 2
    },
    "distinct_sources": 19,
    "mean_seconds": 17.760758933956204,
    "p95_seconds": 80.64794587799993,
    "retries": 20,
    "retry_seconds": 945.743851876
  },
  "smartrecruiters": {
    "attempts": 102,
    "complete": 101,
    "statuses": {
      "SUCCESS_NO_MATCHING_JOBS": 18,
      "SUCCESS": 83,
      "PARSER_ERROR": 1
    },
    "failure_categories": {
      "COMPLETE_SUCCESS": 101,
      "PARSER_ERROR": 1
    },
    "distinct_sources": 15,
    "mean_seconds": 11.944870284980391,
    "p95_seconds": 67.55770057399997,
    "retries": 1,
    "retry_seconds": 67.55770057399997
  },
  "talentbrew": {
    "attempts": 301,
    "complete": 243,
    "statuses": {
      "PARSER_ERROR": 57,
      "SUCCESS": 161,
      "SUCCESS_NO_MATCHING_JOBS": 82,
      "UNKNOWN_ERROR": 1
    },
    "failure_categories": {
      "INVENTORY_COMPLETENESS": 24,
      "COMPLETE_SUCCESS": 243,
      "UNKNOWN_ERROR": 1,
      "PARSER_ERROR": 3,
      "DETAIL_HYDRATION": 30
    },
    "distinct_sources": 42,
    "mean_seconds": 4.801547110833888,
    "p95_seconds": 17.332726934999997,
    "retries": 54,
    "retry_seconds": 97.30804156300005
  },
  "workday": {
    "attempts": 994,
    "complete": 824,
    "statuses": {
      "SUCCESS": 669,
      "BLOCKED": 25,
      "PARSER_ERROR": 77,
      "SUCCESS_NO_MATCHING_JOBS": 155,
      "ATS_CHANGED": 12,
      "HTTP_ERROR": 50,
      "TIMEOUT": 6
    },
    "failure_categories": {
      "COMPLETE_SUCCESS": 824,
      "ACCESS_RESTRICTION": 25,
      "REQUISITION_IDENTITY": 19,
      "PUBLICATION_CHANGED_OR_UNAVAILABLE": 12,
      "HTTP_ERROR": 50,
      "PARSER_ERROR": 50,
      "TIMEOUT": 6,
      "INVENTORY_COMPLETENESS": 8
    },
    "distinct_sources": 155,
    "mean_seconds": 23.008100891440645,
    "p95_seconds": 90.64410357400003,
    "retries": 113,
    "retry_seconds": 4534.95506303
  }
}
```

## Backlog causes

```json
{
  "AWAITING_SCHEDULER_OPPORTUNITY": 40,
  "SOURCE_FAILURE_RETRY": 62
}
```

Data are evaluated at this report clock. Historical successful delivery does not promise a job remains open. The checkpoint report precedes its own final GitHub publication; the following run collects its completed platform record.
