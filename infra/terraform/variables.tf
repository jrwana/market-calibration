variable "region" {
  description = "AWS region. us-west-2 (Oregon) is closest to the US West Coast."
  type        = string
  default     = "us-west-2"
}

variable "availability_zone" {
  description = "Lightsail availability zone within the region."
  type        = string
  default     = "us-west-2a"
}

variable "name" {
  description = "Prefix for every Lightsail resource."
  type        = string
  default     = "market-calibration"
}

variable "bundle_id" {
  description = "Lightsail plan. micro = 1 GB RAM, enough for Streamlit + pandas on a 320 KB file."
  type        = string
  default     = "micro_3_0"
}

variable "ssh_public_key_path" {
  description = "Public key installed for the ubuntu user."
  type        = string
  default     = "~/.ssh/id_ed25519.pub"
}

variable "admin_cidrs" {
  description = "Addresses allowed to SSH in, e.g. [\"203.0.113.7/32\"]. Keep this tight."
  type        = list(string)

  validation {
    condition     = length(var.admin_cidrs) > 0 && !contains(var.admin_cidrs, "0.0.0.0/0")
    error_message = "Set admin_cidrs to your own IP (x.x.x.x/32); SSH open to the world is refused."
  }
}

variable "repo_url" {
  description = "Public git URL the server clones and deploys from."
  type        = string
  default     = "https://github.com/jrwana/market-calibration.git"
}
