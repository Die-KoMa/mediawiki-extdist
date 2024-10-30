{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-24.05";
    utils.url = "github:gytis-ivaskevicius/flake-utils-plus";
    poetry2nix = {
      url = "github:nix-community/poetry2nix";
      inputs = {
        nixpkgs.follows = "nixpkgs";
        flake-utils.follows = "utils/flake-utils";
      };
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      utils,
      poetry2nix,
      ...
    }@inputs:
    let
      inherit (utils.lib) mkFlake exportPackages;
    in
    mkFlake {
      inherit self inputs;

      channels.nixpkgs.overlaysBuilder = channels: [
        poetry2nix.overlays.default
        self.overlays.default
      ];

      outputsBuilder =
        channels:
        let
          system = channels.nixpkgs.system;
        in
        {
          devShells.default =
            let
              pkgs = channels.nixpkgs;
              poetryEnv = pkgs.poetry2nix.mkPoetryEnv { projectDir = ./.; };
            in
            poetryEnv.env.overrideAttrs (oldAttrs: {
              buildInputs = with pkgs; [
                black
                poetry
              ];
            });

          packages = exportPackages { inherit (self.overlays) default; } channels;
        };

      overlays.default = final: prev: {
        mediawiki-extdist = final.poetry2nix.mkPoetryApplication { projectDir = ./.; };
      };

    };
}
