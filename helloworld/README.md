The helloworld topology models a simple 2 VM topology as an introductory example.  

The model consists of the following configurations:
- `helloworld.yaml` - This defines the network topology for the helloworld model.

The model uses the following backing images:
- `ubuntu.qc2` - headless Ubuntu Noble, built by the `ubuntu` recipe in [sceptre-phenix-images](https://github.com/sandialabs/sceptre-phenix-images)

To build it, clone sceptre-phenix-images under `/phenix`, so that the phenix container sees the recipe files at the same path, and run `make ubuntu` there:

```shell
git clone https://github.com/sandialabs/sceptre-phenix-images.git /phenix/sceptre-phenix-images
cd /phenix/sceptre-phenix-images
make ubuntu
mv ubuntu.qc2 /phenix/images/
```

> [!NOTE]
> These examples assume the [sceptre-phenix-topologies](https://github.com/sandialabs/sceptre-phenix-topologies/) git repository has been cloned into `/phenix/topologies/` or the files have been manually copied into `/phenix/topologies/`.
> For example: `git clone https://github.com/sandialabs/sceptre-phenix-topologies/ /phenix/topologies`


To run the helloworld model, `phenix config create` first creates the `helloworld` Topology config in the phenix database (only needed once), then `phenix experiment create` creates a phenix Experiment, referencing the `helloworld` Topology from the phenix database.

```shell
phenix config create /phenix/topologies/helloworld/helloworld.yaml
phenix experiment create hello-world -t helloworld
phenix experiment start hello-world

# To stop:
phenix experiment stop hello-world
```

To run it again, `phenix experiment start hello-world` restarts the stopped experiment. To recreate it, for example after editing the topology, delete it and create it again from the stored Topology:

```shell
phenix experiment delete hello-world
phenix experiment create hello-world -t helloworld
phenix experiment start hello-world
```

> [!NOTE]
> `phenix experiment create hello-world -t /phenix/topologies/helloworld/helloworld.yaml` also works, but it creates the `helloworld` Topology config too, so it fails with `config already exists` once that config is in the phenix database.
> After editing `helloworld.yaml`, run `phenix config delete topology/helloworld` before `phenix config create` again.
