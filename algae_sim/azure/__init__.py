"""The Azure version of the instrument data pipeline (optional; ``uv sync --extra azure``).

The uploader on the laptop does what an Azure Function would. A Flex Consumption
Function isn't available on the free trial, so the laptop works directly:

* keep each batch exactly as received, in Blob Storage (``raw`` container);
* parse it with the same ``algae_sim.pipeline.parse`` rules as the local pipeline;
* stream clean rows into Azure Data Explorer, where materialised views drop
  duplicates on (device_id, sequence) and KQL functions give the per-hold report.

Sign-in is one browser login with Microsoft Entra ID, used for both services,
so no keys or connection strings exist anywhere. The core ``algae_sim`` package
doesn't import this subpackage; only the uploader's ``--azure`` option does.
"""
