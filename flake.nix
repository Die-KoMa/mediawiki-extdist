{
  inputs = {
    nixpkgs.url = "https://channels.nixos.org/nixos-26.05/nixexprs.tar.xz";
  };

  outputs =
    inputs@{
      self,
      nixpkgs,
    }:
    let
      forAllSystems =
        f:
        nixpkgs.lib.genAttrs
          [
            "x86_64-linux"
            "aarch64-linux"
          ]
          (
            system:
            let
              pkgs = import nixpkgs {
                inherit system;
                overlays = [
                  self.overlays.default
                ];
              };

            in
            f pkgs
          );
    in
    {
      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell {
          inputsFrom = [ pkgs.mediawiki-extdist ];
          packages = [
            pkgs.black
            pkgs.poetry
          ];
        };

      });

      packages = forAllSystems (pkgs: {
        inherit (pkgs) mediawiki-extdist;
      });

      overlays = {
        default =
          final: prev:
          let
            python = final.python3;
          in
          {
            mediawiki-extdist = python.pkgs.buildPythonApplication {
              name = "mediawiki-extdist";
              version = "0.0.1";
              src = ./.;
              pyproject = true;

              meta = {
                license = final.lib.licenses.gpl3Plus;
                mainProgram = "mediawiki-extdist";
              };

              build-system = [ python.pkgs.poetry-core ];
              dependencies = final.lib.attrValues {
                inherit (python.pkgs)
                  requests
                  ;
              };
            };
          };
      };
    };
}
