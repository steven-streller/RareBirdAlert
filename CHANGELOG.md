# Changelog

## [0.1.10](https://github.com/steven-streller/RareBirdAlert/compare/v0.1.9...v0.1.10) (2026-07-26)


### Features

* add LOG_LEVEL env var to control log verbosity ([#46](https://github.com/steven-streller/RareBirdAlert/issues/46)) ([fdf95cb](https://github.com/steven-streller/RareBirdAlert/commit/fdf95cb8e56e4c939c8dff6fb71f43df2a211f4f))
* add rate limiting to the registration endpoint ([#48](https://github.com/steven-streller/RareBirdAlert/issues/48)) ([3bfdf9e](https://github.com/steven-streller/RareBirdAlert/commit/3bfdf9ef92994e2d373a9f43d4e3bd4fff40f3af))

## [0.1.9](https://github.com/steven-streller/RareBirdAlert/compare/v0.1.8...v0.1.9) (2026-07-26)


### Features

* add a /readyz endpoint with a real database check ([#43](https://github.com/steven-streller/RareBirdAlert/issues/43)) ([ac2f9d1](https://github.com/steven-streller/RareBirdAlert/commit/ac2f9d10c352fcebfd3862b2e331e024b37ec6aa))
* add structured JSON logging (LOG_FORMAT=json) ([#45](https://github.com/steven-streller/RareBirdAlert/issues/45)) ([4df9cd5](https://github.com/steven-streller/RareBirdAlert/commit/4df9cd50463e88bf8ea53fb863a3610508ff1bab))

## [0.1.8](https://github.com/steven-streller/RareBirdAlert/compare/v0.1.7...v0.1.8) (2026-07-26)


### Bug Fixes

* use consistent port 8000 in docker-compose and docs ([#40](https://github.com/steven-streller/RareBirdAlert/issues/40)) ([73a53e7](https://github.com/steven-streller/RareBirdAlert/commit/73a53e746e88cfc81b3bec184e8d0355e39ab4cb))

## [0.1.7](https://github.com/steven-streller/RareBirdAlert/compare/v0.1.6...v0.1.7) (2026-07-26)


### Features

* add a Prometheus /metrics endpoint ([#35](https://github.com/steven-streller/RareBirdAlert/issues/35)) ([db2790c](https://github.com/steven-streller/RareBirdAlert/commit/db2790c025bc26ef3fa80603ed3722a04f2b4dd6))
* add automatic SQLite database backups ([#36](https://github.com/steven-streller/RareBirdAlert/issues/36)) ([1d8092e](https://github.com/steven-streller/RareBirdAlert/commit/1d8092e2075f312b1120176195fad975cbc4257b))
* add CSRF protection to all state-changing routes ([#30](https://github.com/steven-streller/RareBirdAlert/issues/30)) ([262875b](https://github.com/steven-streller/RareBirdAlert/commit/262875b3ef7d8d6acb3eca8343d8c3f0b4cfbede))
* add per-user quiet hours for notifications ([#33](https://github.com/steven-streller/RareBirdAlert/issues/33)) ([1daa3ce](https://github.com/steven-streller/RareBirdAlert/commit/1daa3ce55f6ea1b094e896d75fd4c11bbd0fab85))
* add rate limiting to the login endpoint ([#32](https://github.com/steven-streller/RareBirdAlert/issues/32)) ([43d066b](https://github.com/steven-streller/RareBirdAlert/commit/43d066bb7330b36b9d2e9f8fed61665531e59d10))
* add Web Push as a notification channel ([#38](https://github.com/steven-streller/RareBirdAlert/issues/38)) ([fae6f81](https://github.com/steven-streller/RareBirdAlert/commit/fae6f811ba1253635d8b40d4a6aa8d1017823090))
* enrich matched sightings with an aircraft photo via planespotters.net ([#34](https://github.com/steven-streller/RareBirdAlert/issues/34)) ([42791ba](https://github.com/steven-streller/RareBirdAlert/commit/42791ba6bdce2e343d764c2bd793444ef8f2b707))


### Bug Fixes

* dark-style the quiet hours time inputs ([#39](https://github.com/steven-streller/RareBirdAlert/issues/39)) ([8f97efe](https://github.com/steven-streller/RareBirdAlert/commit/8f97efe88961c6ee2e1d4e046304f576c0793427))
* gitignore the default local backup directory ([#37](https://github.com/steven-streller/RareBirdAlert/issues/37)) ([b2fced2](https://github.com/steven-streller/RareBirdAlert/commit/b2fced2605fccd3bb73f3c6dd4afec6f2bf4d4ae))

## [0.1.6](https://github.com/steven-streller/RareBirdAlert/compare/v0.1.5...v0.1.6) (2026-07-25)


### Features

* enrich matched sightings with flight route via adsbdb.com ([#28](https://github.com/steven-streller/RareBirdAlert/issues/28)) ([1819f2d](https://github.com/steven-streller/RareBirdAlert/commit/1819f2db7bcc37c604059412cb94941541269037))

## [0.1.5](https://github.com/steven-streller/RareBirdAlert/compare/v0.1.4...v0.1.5) (2026-07-25)


### Bug Fixes

* URL redirection from remote source in _safe_channel_anchor (CodeQL) ([#26](https://github.com/steven-streller/RareBirdAlert/issues/26)) ([2079b98](https://github.com/steven-streller/RareBirdAlert/commit/2079b98348f8d47999e62284881ec2bdacb2c768))

## [0.1.4](https://github.com/steven-streller/RareBirdAlert/compare/v0.1.3...v0.1.4) (2026-07-25)


### Features

* add airplanes.live as a third data source ([#23](https://github.com/steven-streller/RareBirdAlert/issues/23)) ([0c4183a](https://github.com/steven-streller/RareBirdAlert/commit/0c4183abe4887500fc9b97fb0d0e73a0fed17b03))

## [0.1.3](https://github.com/steven-streller/RareBirdAlert/compare/v0.1.2...v0.1.3) (2026-07-25)


### Features

* add live map view of watched airports and aircraft ([#16](https://github.com/steven-streller/RareBirdAlert/issues/16)) ([8fcdd8a](https://github.com/steven-streller/RareBirdAlert/commit/8fcdd8a81a722c7f2d4c483fd4dfbf7ae171a566))
* introduce an admin account, move poll interval + data sources there ([#21](https://github.com/steven-streller/RareBirdAlert/issues/21)) ([bbcc340](https://github.com/steven-streller/RareBirdAlert/commit/bbcc3403d64193a566b45f165e76f8028b77df01))

## [0.1.2](https://github.com/steven-streller/RareBirdAlert/compare/v0.1.1...v0.1.2) (2026-07-25)


### Features

* add adsb.lol as a second data source, merge multiple sources per poll ([#14](https://github.com/steven-streller/RareBirdAlert/issues/14)) ([dae75cf](https://github.com/steven-streller/RareBirdAlert/commit/dae75cfefe132a3c7419dcced5cea5222bc32313))

## [0.1.1](https://github.com/steven-streller/RareBirdAlert/compare/v0.1.0...v0.1.1) (2026-07-25)


### Features

* initial RareBirdAlert application ([2cba6b4](https://github.com/steven-streller/RareBirdAlert/commit/2cba6b43f91d947bedf0e1916409d2186df6a4d1))
* initial RareBirdAlert application ([5b80697](https://github.com/steven-streller/RareBirdAlert/commit/5b806978c3f2cd0b9dc9969feb7b321a1b684e06))


### Bug Fixes

* update CI workflows to use ubuntu-24.04 ([cdcc7be](https://github.com/steven-streller/RareBirdAlert/commit/cdcc7be776a545e2e7f4d7980abf302b7f102b78))
