# example-app

The sample application used by the quickstart. It exists to show what an
application role owes the framework, not to be useful.

An application role is one directory with three obligations:

1. **`defaults/main.yml`** names the application and its port. Everything else
   is derived from `app_name`.
2. **`meta/main.yml`** declares the dependency on `common/python`, so adding an
   application never edits a playbook.
3. **`tasks/main.yml`** installs the application and then **proves it works**.

The third is the one that matters. The checks in this role cover the failures a
green play hides: a package tree that did not install, a module that cannot be
imported, a service that is up but bound to the wrong port, and a service that
answers nothing.

Replace it with your own application, and keep the checks.
