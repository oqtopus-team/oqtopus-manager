# Cloud Local Environments

A **Cloud Local** environment is an OQTOPUS Cloud deployment that OQTOPUS Manager creates, installs, starts, and
monitors on your behalf. This page walks through the full lifecycle: create, install components, start services,
and check on a running environment.

!!! note
    "Cloud Local" installs and runs OQTOPUS Cloud locally, on-prem, on the machine where OQTOPUS Manager is
    running. It does not install or provision anything on a public cloud.

!!! note "Prerequisites"
    [Docker](https://docs.docker.com/get-docker/) must be installed and running. The `db` service runs MySQL
    and MinIO as Docker containers.

## Create

Click **Cloud Local** in the sidebar. With no environments yet, the list is empty:

![Empty Cloud Local environment list](../asset/screenshots/cloud_local_list_empty.png)

Click **New Cloud Local**, give the environment a name, and submit:

![New Cloud Local environment form](../asset/screenshots/cloud_local_new_form.png)

The name is passed to `oqtopus init`, and its output streams live in the page:

![Environment creation console output](../asset/screenshots/environment_create_console.png)

The environment now appears in the list. Its **Service Status** is blank because no components have been
installed yet:

![Populated Cloud Local environment list, not yet installed](../asset/screenshots/cloud_local_list_populated.png)

## Install components

Open the environment's detail page. The **Settings** panel shows where it lives on disk, and the **Components**
panel lets you install the `cloud`, `frontend`, and `admin` components, individually or all at once, optionally
pinned to a specific version:

![Cloud Local environment detail page, components not installed](../asset/screenshots/cloud_local_detail_not_installed.png)

Pick a component (or `all`), optionally a version, and click **Install**. Installing runs `oqtopus cloud-local
install` and streams its output the same way environment creation does.

## Start services

Once every component is installed, the **Service Status** panel appears with every service stopped:

![Cloud Local environment detail page, components installed but services stopped](../asset/screenshots/cloud_local_detail_installed_stopped.png)

Click **Start all** (or start/stop/restart individual services: `db`, `worker`, `user_signup`, `admin`,
`provider`, `user`). Once running, the detail page reflects it:

![Cloud Local environment detail page, components installed and services running](../asset/screenshots/cloud_local_detail_running.png)

The list page reflects it too:

![Populated Cloud Local environment list, services running](../asset/screenshots/cloud_local_list_running.png)

## View .env

The **.env** button on the environment detail page opens the environment's `config/.env` file, with a
diff-with-release view, download, and editing:

![Cloud Local .env viewer](../asset/screenshots/cloud_local_dotenv.png)

## View logs

Each service row has a **Log** button that opens a log viewer that updates live as new lines are written, with
highlighting, auto-scroll, and download:

![Cloud Local service log viewer](../asset/screenshots/cloud_local_service_log.png)

## Delete

**Delete** on the list or detail page removes the environment's registration and on-disk directory after a
confirmation prompt. If any service is still running, deletion fails with an error. Stop all services first,
then delete.
