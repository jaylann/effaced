# Changelog

## 0.1.0 (2026-06-11)


### Features

* adopt sync-first session strategy per adr 0006 ([#21](https://github.com/jaylann/effaced/issues/21)) ([808e7bc](https://github.com/jaylann/effaced/commit/808e7bc7400042f05d92f02586195d3bcb1ee5cf))
* **consent:** implement ConsentLedger + DatabaseAuditSink ([#26](https://github.com/jaylann/effaced/issues/26)) ([ddfacb9](https://github.com/jaylann/effaced/commit/ddfacb9c3967ed8638b85d07470e222643c4f1a5))
* **erasure:** erase_subject() — atomic local phase + transactional outbox enqueue ([#31](https://github.com/jaylann/effaced/issues/31)) ([de549a9](https://github.com/jaylann/effaced/commit/de549a9035e8750eeff27b179fb6326ddf91f02f))
* **erasure:** implement plan() — inspectable fk-safe plans ([#27](https://github.com/jaylann/effaced/issues/27)) ([bf6a5f6](https://github.com/jaylann/effaced/commit/bf6a5f677256ad43a71956acb93737a39d1d5a15))
* **export:** implement Exporter (Art. 15) ([#30](https://github.com/jaylann/effaced/issues/30)) ([1eb61d7](https://github.com/jaylann/effaced/commit/1eb61d723aa6eb70e3452e9a7012157cd16c57a3))
* **lint:** completeness linter + assert_data_map_complete ci gate ([#52](https://github.com/jaylann/effaced/issues/52)) ([29d0f1a](https://github.com/jaylann/effaced/commit/29d0f1aba35e7ab5a689cb47fde5c0096b772226))
* **manifest:** subject-link graph resolution + fk-safe ordering ([#23](https://github.com/jaylann/effaced/issues/23)) ([f71cc20](https://github.com/jaylann/effaced/commit/f71cc2068602229dcd3c6315f76db627651d2fa6))
* **saga:** outbox claim_batch + saga runner with retries and abandonment ([#33](https://github.com/jaylann/effaced/issues/33)) ([606cfd5](https://github.com/jaylann/effaced/commit/606cfd58c9debf57166de5a4c384f6e1053cd086))
* **saga:** read-only operator surface for the outbox ([#53](https://github.com/jaylann/effaced/issues/53)) ([a87ae1c](https://github.com/jaylann/effaced/commit/a87ae1c54eb8af06e0bf7119602876d8d12b5e11))
* **storage:** effaced-owned tables + bind_tables(metadata) ([#24](https://github.com/jaylann/effaced/issues/24)) ([7360aae](https://github.com/jaylann/effaced/commit/7360aae72a4517fcc5de2f35852c08c39b62a1bc))
* **stripe:** implement StripeResolver + shared resolver conformance suite ([#35](https://github.com/jaylann/effaced/issues/35)) ([2d7391e](https://github.com/jaylann/effaced/commit/2d7391efea2b24414b1d7f2dc7a9cb23b41da73a))


### Documentation

* make quickstart + readme executable and true ([#42](https://github.com/jaylann/effaced/issues/42)) ([3af95e1](https://github.com/jaylann/effaced/commit/3af95e136722dbc40d938c0306cc41e977433a91))

## Changelog

All notable changes to `effaced` are documented here. Managed by
[release-please](https://github.com/googleapis/release-please); entries are
generated from Conventional Commits.

Erasure/export behaviour changes are always called out under **Security**
sections — a change to what gets deleted or exported is a data-protection
change and is treated as MAJOR.
