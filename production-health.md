# Scout production health

Generated 2026-11-27T10:28:27+00:00

**VERIFIED_NATURAL_EXECUTION**

Next configured opportunity: 2026-11-27T08:17:00-05:00 (America/New_York); 2026-11-27T13:17:00+00:00 UTC.

Healthy certified employers: 109; scheduled boards: 336; source states: {'STALE': 226, 'HEALTHY_COMPLETE': 89, 'FAILED': 19, 'BLOCKED': 2}.
Due boards: 225; requested/effective cadence misses: 231 / 231.

New source-membership events: 65191; relevant internships: 340; verified public deliveries: 327; timestamp-qualified latency observations: 14.

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
    "attempts": 15,
    "complete": 15,
    "statuses": {
      "SUCCESS": 9,
      "SUCCESS_NO_MATCHING_JOBS": 6
    },
    "failure_categories": {
      "COMPLETE_SUCCESS": 15
    },
    "distinct_sources": 2,
    "mean_seconds": 0.31236252699999373,
    "p95_seconds": 0.6970381700000019,
    "retries": 0,
    "retry_seconds": 0
  },
  "eightfold": {
    "attempts": 90,
    "complete": 69,
    "statuses": {
      "PARSER_ERROR": 21,
      "SUCCESS": 59,
      "SUCCESS_NO_MATCHING_JOBS": 10
    },
    "failure_categories": {
      "PARSER_ERROR": 12,
      "COMPLETE_SUCCESS": 69,
      "INVENTORY_COMPLETENESS": 9
    },
    "distinct_sources": 9,
    "mean_seconds": 17.92291540413333,
    "p95_seconds": 63.662714986999994,
    "retries": 20,
    "retry_seconds": 409.53531047999996
  },
  "greenhouse": {
    "attempts": 229,
    "complete": 210,
    "statuses": {
      "SUCCESS": 179,
      "SUCCESS_NO_MATCHING_JOBS": 31,
      "ATS_CHANGED": 14,
      "PARSER_ERROR": 5
    },
    "failure_categories": {
      "COMPLETE_SUCCESS": 210,
      "PUBLICATION_CHANGED_OR_UNAVAILABLE": 14,
      "PARSER_ERROR": 5
    },
    "distinct_sources": 27,
    "mean_seconds": 0.816497941685589,
    "p95_seconds": 2.5135566660000093,
    "retries": 18,
    "retry_seconds": 2.3769455409999254
  },
  "icims": {
    "attempts": 338,
    "complete": 328,
    "statuses": {
      "SUCCESS": 124,
      "SUCCESS_NO_MATCHING_JOBS": 204,
      "PARSER_ERROR": 10
    },
    "failure_categories": {
      "COMPLETE_SUCCESS": 328,
      "PARSER_ERROR": 10
    },
    "distinct_sources": 49,
    "mean_seconds": 2.5710252818609476,
    "p95_seconds": 9.272385449999998,
    "retries": 9,
    "retry_seconds": 10.627064282999978
  },
  "lever": {
    "attempts": 51,
    "complete": 51,
    "statuses": {
      "SUCCESS": 34,
      "SUCCESS_NO_MATCHING_JOBS": 17
    },
    "failure_categories": {
      "COMPLETE_SUCCESS": 51
    },
    "distinct_sources": 7,
    "mean_seconds": 3.032269724117643,
    "p95_seconds": 9.386387360000004,
    "retries": 1,
    "retry_seconds": 3.133721312000003
  },
  "oracle": {
    "attempts": 101,
    "complete": 100,
    "statuses": {
      "SUCCESS": 90,
      "SUCCESS_NO_MATCHING_JOBS": 10,
      "PARSER_ERROR": 1
    },
    "failure_categories": {
      "COMPLETE_SUCCESS": 100,
      "INVENTORY_COMPLETENESS": 1
    },
    "distinct_sources": 11,
    "mean_seconds": 13.540750931396039,
    "p95_seconds": 30.919987173,
    "retries": 1,
    "retry_seconds": 31.27330887700002
  },
  "phenom": {
    "attempts": 194,
    "complete": 166,
    "statuses": {
      "PARSER_ERROR": 24,
      "SUCCESS": 149,
      "SUCCESS_NO_MATCHING_JOBS": 17,
      "TIMEOUT": 1,
      "NETWORK_ERROR": 1,
      "ATS_CHANGED": 2
    },
    "failure_categories": {
      "INVENTORY_COMPLETENESS": 18,
      "COMPLETE_SUCCESS": 166,
      "TIMEOUT": 1,
      "DNS": 1,
      "PARSER_ERROR": 6,
      "PUBLICATION_CHANGED_OR_UNAVAILABLE": 2
    },
    "distinct_sources": 19,
    "mean_seconds": 15.877771715051546,
    "p95_seconds": 73.28704669000001,
    "retries": 28,
    "retry_seconds": 1061.565185214
  },
  "smartrecruiters": {
    "attempts": 152,
    "complete": 151,
    "statuses": {
      "SUCCESS_NO_MATCHING_JOBS": 29,
      "SUCCESS": 122,
      "PARSER_ERROR": 1
    },
    "failure_categories": {
      "COMPLETE_SUCCESS": 151,
      "PARSER_ERROR": 1
    },
    "distinct_sources": 15,
    "mean_seconds": 12.49807684218421,
    "p95_seconds": 67.536418065,
    "retries": 1,
    "retry_seconds": 67.55770057399997
  },
  "talentbrew": {
    "attempts": 446,
    "complete": 365,
    "statuses": {
      "PARSER_ERROR": 79,
      "SUCCESS": 245,
      "SUCCESS_NO_MATCHING_JOBS": 120,
      "UNKNOWN_ERROR": 2
    },
    "failure_categories": {
      "INVENTORY_COMPLETENESS": 39,
      "COMPLETE_SUCCESS": 365,
      "UNKNOWN_ERROR": 2,
      "PARSER_ERROR": 4,
      "DETAIL_HYDRATION": 36
    },
    "distinct_sources": 42,
    "mean_seconds": 5.03893692571749,
    "p95_seconds": 18.501257567999996,
    "retries": 77,
    "retry_seconds": 137.13041127700012
  },
  "workday": {
    "attempts": 1466,
    "complete": 1262,
    "statuses": {
      "SUCCESS": 1028,
      "BLOCKED": 38,
      "PARSER_ERROR": 96,
      "SUCCESS_NO_MATCHING_JOBS": 234,
      "ATS_CHANGED": 14,
      "HTTP_ERROR": 50,
      "TIMEOUT": 6
    },
    "failure_categories": {
      "COMPLETE_SUCCESS": 1262,
      "ACCESS_RESTRICTION": 38,
      "REQUISITION_IDENTITY": 29,
      "PUBLICATION_CHANGED_OR_UNAVAILABLE": 14,
      "HTTP_ERROR": 50,
      "PARSER_ERROR": 55,
      "TIMEOUT": 6,
      "INVENTORY_COMPLETENESS": 12
    },
    "distinct_sources": 155,
    "mean_seconds": 23.21315976165348,
    "p95_seconds": 90.641288939,
    "retries": 199,
    "retry_seconds": 7315.490189417
  }
}
```

## Backlog causes

```json
{
  "AWAITING_SCHEDULER_OPPORTUNITY": 212,
  "SOURCE_FAILURE_RETRY": 13
}
```

Data are evaluated at this report clock. Historical successful delivery does not promise a job remains open. The checkpoint report precedes its own final GitHub publication; the following run collects its completed platform record.
