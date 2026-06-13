# Changelog

All notable changes to effaced-django are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/), and the project
adheres to [Semantic Versioning](https://semver.org/) — widened so that any change to
*what gets deleted or exported* is a major version.

## [Unreleased]

### Added

- Initial Django ORM adapter: `@effaced_model`/`pii`/`subject_link` authoring on Django
  models, `Model._meta` -> SQLAlchemy metadata translation, and `DjangoEffacedStack`
  wiring every engine via the foreign-key subject-graph resolver.
