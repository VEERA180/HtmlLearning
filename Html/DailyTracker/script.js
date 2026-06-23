function addTask()
{
    let input = document.getElementById("taskInput");
    let taskText = input.value;

    if(taskText == "")
    {
        alert("Please enter your task...")
        returns;
    }

    let li = document.createElement("li");
    li.textContent=taskText;

    document.getElementById("taskList").append(li);
    input.value = "";

    li.onclick =function()
    {
        li.classList.toggle("completed")
    }
}