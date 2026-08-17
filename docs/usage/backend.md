# Backend Environments

A **Backend** environment is an OQTOPUS Backend deployment that OQTOPUS Manager creates, installs, starts, and
monitors on your behalf. This page walks through the full lifecycle: create, install components, start services,
and check on a running environment.

!!! note "Prerequisites"
    [Docker](https://docs.docker.com/get-docker/) must be installed and running. **Build SSE Runtime** builds
    a Docker image that the `sse_engine` service uses.

## Create

Click **Backend** in the sidebar. With no environments yet, the list is empty:

![Empty Backend environment list](../asset/screenshots/backend_list_empty.png)

Click **New Backend**, give the environment a name, and submit:

![New Backend environment form](../asset/screenshots/backend_new_form.png)

The name is passed to `oqtopus init`, and its output streams live in the page:

![Backend environment creation console output](../asset/screenshots/backend_create_console.png)

The environment now appears in the list. Its **Device Status** and **Service Status** are blank because no
components have been installed yet:

![Populated Backend environment list, not yet installed](../asset/screenshots/backend_list_populated.png)

## Install components

Open the environment's detail page. The **Settings** panel shows where it lives on disk, and the **Components**
panel lets you install the `engine`, `tranqu`, and `gateway` components, individually or all at once, optionally
pinned to a specific version:

![Backend environment detail page, components not installed](../asset/screenshots/backend_detail_not_installed.png)

Pick a component (or `all`), optionally a version, and click **Install**. Installing runs `oqtopus backend
install` and streams its output the same way environment creation does.

Once `engine` is installed, **Build SSE Runtime** becomes available. Click it to build the Docker image the
`sse_engine` service uses to run submitted programs.

## Start services

With every component installed and built, **Device Status** and **Service Status** appear, and every service
starts out stopped:

![Backend environment detail page, components installed but services stopped](../asset/screenshots/backend_detail_installed_stopped.png)

In **Service Status**, click **Start all** (or start/stop/restart individual services: `core`, `sse_engine`,
`mitigator`, `estimator`, `combiner`, `tranqu`, `gateway`). Once running, the detail page reflects it:

![Backend environment detail page, components installed and services running](../asset/screenshots/backend_detail_running.png)

The list page reflects it too:

![Populated Backend environment list, services running](../asset/screenshots/backend_list_running.png)

## Device status

The **Device Status** panel shows the device's current status as reported by the gateway service, with buttons
to set it to `active`, `inactive`, or `maintenance`. This is the same status shown in the list's **Device
Status** column.

## View .env

The **.env** button on the environment detail page opens the environment's `config/.env` file, with a
diff-with-release view, download, and editing:

![Backend .env viewer](../asset/screenshots/backend_dotenv.png)

## View service config

Each service row in **Service Status** has a **Config** button that opens its `config.yaml` and `logging.yaml`,
with the same diff-with-release view, download, and editing as the `.env` editor. For `gateway`, this is also
where the device backend (`qulacs` or `qubex`), device info, and topology settings live:

![Backend gateway config editor](../asset/screenshots/backend_gateway_config.png)

## View logs

Each service row also has a **Log** button that opens a log viewer that updates live as new lines are written,
with highlighting, auto-scroll, and download:

![Backend service log viewer](../asset/screenshots/backend_service_log.png)

## Delete

**Delete** on the list or detail page removes the environment's registration and on-disk directory after a
confirmation prompt. If any service is still running, deletion fails with an error. Stop all services first,
then delete.
