# One small Lightsail instance running the dashboard in Docker behind Caddy (see deploy/).
# Lightsail rather than EC2: a flat monthly price that includes the IPv4 address and bandwidth,
# which is the right trade for a single small container.

resource "aws_lightsail_key_pair" "deploy" {
  name       = "${var.name}-key"
  public_key = file(pathexpand(var.ssh_public_key_path))
}

resource "aws_lightsail_instance" "app" {
  name              = var.name
  availability_zone = var.availability_zone
  blueprint_id      = "ubuntu_24_04"
  bundle_id         = var.bundle_id
  key_pair_name     = aws_lightsail_key_pair.deploy.name

  # Installs Docker and clones the repo. It does not start the app: secrets (the URL path and
  # hostname) are written later by deploy/deploy.sh, so they never live in Terraform state or git.
  user_data = templatefile("${path.module}/cloud-init.sh.tftpl", {
    repo_url = var.repo_url
  })
}

# A static IP keeps the address (and the HTTPS certificate's hostname) stable across reboots.
# Free while attached to a running instance.
resource "aws_lightsail_static_ip" "app" {
  name = "${var.name}-ip"
}

resource "aws_lightsail_static_ip_attachment" "app" {
  static_ip_name = aws_lightsail_static_ip.app.name
  instance_name  = aws_lightsail_instance.app.name

  # The instance is referenced by name, which survives a replacement, so without this Terraform
  # would keep believing the IP is attached to the instance it just deleted.
  lifecycle {
    replace_triggered_by = [aws_lightsail_instance.app.id]
  }
}

# Replaces Lightsail's default firewall (which leaves SSH open to everyone).
resource "aws_lightsail_instance_public_ports" "app" {
  instance_name = aws_lightsail_instance.app.name

  # Same reason as the IP attachment: a new instance starts with the default (open SSH) firewall.
  lifecycle {
    replace_triggered_by = [aws_lightsail_instance.app.id]
  }

  port_info {
    protocol  = "tcp"
    from_port = 22
    to_port   = 22
    cidrs     = var.admin_cidrs
  }

  # 80 stays open so Caddy can complete the Let's Encrypt challenge and redirect to HTTPS.
  port_info {
    protocol  = "tcp"
    from_port = 80
    to_port   = 80
    cidrs     = ["0.0.0.0/0"]
  }

  port_info {
    protocol  = "tcp"
    from_port = 443
    to_port   = 443
    cidrs     = ["0.0.0.0/0"]
  }
}
