import vgamepad
import time

gamepad = vgamepad.VX360Gamepad()

# Define button mappings
action_map = {
    "up": vgamepad.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_UP,
    "down": vgamepad.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_DOWN,
    "left": vgamepad.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_LEFT,
    "right": vgamepad.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_RIGHT,
    "attack": vgamepad.XUSB_BUTTON.XUSB_GAMEPAD_A,
    "jump": vgamepad.XUSB_BUTTON.XUSB_GAMEPAD_B,
    "special": vgamepad.XUSB_BUTTON.XUSB_GAMEPAD_X,
    "block": vgamepad.XUSB_BUTTON.XUSB_GAMEPAD_Y,
    "start": vgamepad.XUSB_BUTTON.XUSB_GAMEPAD_START,  # menu navigation only, not in the agent's action space
}

axis_map = {
    "Axis_0": (-1, 0),  # Left stick left
    "Axis_1": (1, 0),   # Left stick right
}

def send_input(action_id):
    """Send a controller input based on the predicted action ID."""
    if action_id in action_map:
        print(f"Pressing: {action_id} -> {action_map[action_id]}")
        gamepad.press_button(button=action_map[action_id])
        gamepad.update()
        time.sleep(0.1)  # Simulate button press duration
        gamepad.release_button(button=action_map[action_id])
        gamepad.update()
    elif action_id in axis_map:
        print(f"Moving joystick: {action_id} -> {axis_map[action_id]}")
        x, y = axis_map[action_id]
        gamepad.left_joystick(x_value=int(x * 32767), y_value=int(y * 32767))
        gamepad.update()
        time.sleep(0.1)  # Hold joystick direction briefly
        gamepad.left_joystick(x_value=0, y_value=0)  # Reset joystick
        gamepad.update()
