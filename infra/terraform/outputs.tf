output "public_ip" {
  description = "Static IP of the server. Point your DNS record here, or use the sslip.io name."
  value       = aws_lightsail_static_ip.app.ip_address
}

output "sslip_hostname" {
  description = "A hostname that resolves to the server with no DNS setup (works for HTTPS)."
  value       = "${replace(aws_lightsail_static_ip.app.ip_address, ".", "-")}.sslip.io"
}

output "ssh" {
  value = "ssh ubuntu@${aws_lightsail_static_ip.app.ip_address}"
}
