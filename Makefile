help:
	python main.py --help

start:
	python main.py
	
rabbitmq:
	podman run --rm -d \
			--hostname heybilly-rabbit \
            --name heybilly-rabbit \
            -p 15672:15672 -p 5672:5672 \
            rabbitmq:3-management