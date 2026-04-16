#!/bin/bash
# PaddleOCR EC2 instance bootstrap script
# Pulls image from ECR and runs PaddleOCR API server on port 8866

set -ex

# Install Docker
yum update -y
yum install -y docker
systemctl enable docker
systemctl start docker

# ECR login and pull
REGION="ap-northeast-2"
ACCOUNT="863518448167"
ECR_URI="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com/knowledge-local/paddleocr:latest"

aws ecr get-login-password --region ${REGION} | docker login --username AWS --password-stdin ${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com

docker pull ${ECR_URI}

# Run PaddleOCR API server
docker run -d \
  --name paddleocr \
  --restart unless-stopped \
  -p 8866:8866 \
  --cpus 3 \
  --memory 6g \
  ${ECR_URI}

# Create systemd service for auto-start after reboot
cat > /etc/systemd/system/paddleocr.service << 'EOF'
[Unit]
Description=PaddleOCR API Server
After=docker.service
Requires=docker.service

[Service]
Restart=always
ExecStart=/usr/bin/docker start -a paddleocr
ExecStop=/usr/bin/docker stop paddleocr

[Install]
WantedBy=multi-user.target
EOF

systemctl enable paddleocr
